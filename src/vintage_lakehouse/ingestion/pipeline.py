"""Ingestion pipeline: load observations into a store, detect revisions, record lineage.

A source hands over *chunks* (one request's worth of observations, plus the raw bytes they were
parsed from). For each chunk the pipeline

1. compares every new version with the version that preceded it, and classifies it as a new
   period, a revision (value changed) or an unchanged republication;
2. skips versions that are already stored, so re-ingesting a chunk is a no-op (idempotent);
3. stores the new versions in one atomic ``append``;
4. returns an ``IngestReport`` that ``run_ingestion`` writes to the lineage log.

A version that clashes with a stored one (same key, different value or source) means a source
changed its published history. That is reported as a failed run and nothing from the chunk is
stored.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum

from vintage_lakehouse.ingestion.archive import RawArchive
from vintage_lakehouse.ingestion.lineage import IngestReport, LineageLog
from vintage_lakehouse.storage.base import AsOfStore, ConflictError
from vintage_lakehouse.storage.model import Observation


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Kind(str, Enum):
    """How a new version relates to the version before it."""

    NEW_PERIOD = "new_period"
    REVISED = "revised"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class Chunk:
    """One request's worth of observations and the raw bytes they came from."""

    request: str
    raw: bytes | None
    observations: list[Observation]


class IngestFailed(RuntimeError):
    """A chunk could not be ingested. ``report`` has the details and was already logged."""

    def __init__(self, report: IngestReport, reports: list[IngestReport]) -> None:
        super().__init__(f"ingestion failed for {report.request}: {report.error}")
        self.report = report
        self.reports = reports


def classify(previous: Observation | None, new: Observation) -> Kind:
    """Classify ``new`` against ``previous``, the latest version known before it."""
    if previous is None:
        return Kind.NEW_PERIOD
    return Kind.REVISED if previous.value != new.value else Kind.UNCHANGED


def ingest(
    store: AsOfStore,
    observations: Iterable[Observation],
    *,
    source: str,
    request: str,
    raw_sha256: str | None = None,
    run_id: str | None = None,
    clock: Callable[[], datetime] = _now,
) -> IngestReport:
    """Ingest one chunk and return its report. Never partially stores a chunk."""
    started = clock()
    items = list(observations)
    for item in items:
        if not isinstance(item, Observation):
            raise TypeError(f"expected an Observation, got {type(item).__name__}")

    by_period: dict[tuple[str, date], list[Observation]] = defaultdict(list)
    for item in items:
        by_period[(item.series_id, item.event_time)].append(item)

    counts: dict[Kind, int] = dict.fromkeys(Kind, 0)
    duplicates = 0
    max_revision: float | None = None
    to_store: list[Observation] = []
    stored = 0
    error: str | None = None

    try:
        for (series_id, event_time), group in by_period.items():
            known = {v.knowledge_time: v for v in store.revisions(series_id, event_time)}
            for new in sorted(group, key=lambda o: o.knowledge_time):
                existing = known.get(new.knowledge_time)
                if existing is not None:
                    if (existing.value, existing.source) != (new.value, new.source):
                        raise ConflictError(
                            f"version {new.key} is already stored with a different value or source"
                        )
                    duplicates += 1
                    continue
                earlier = [k for k in known if k < new.knowledge_time]
                previous = known[max(earlier)] if earlier else None
                kind = classify(previous, new)
                counts[kind] += 1
                if kind is Kind.REVISED and previous is not None:
                    size = abs(new.value - previous.value)
                    max_revision = size if max_revision is None else max(max_revision, size)
                known[new.knowledge_time] = new
                to_store.append(new)

        stamp = started
        stamped = [
            item if item.ingest_time is not None else dataclasses.replace(item, ingest_time=stamp)
            for item in to_store
        ]
        stored = store.append(stamped)
    except ConflictError as exc:
        error = str(exc)

    failed = error is not None
    return IngestReport(
        run_id=run_id or uuid.uuid4().hex,
        source=source,
        request=request,
        started_at=started,
        finished_at=clock(),
        status="failed" if failed else "ok",
        error=error,
        rows_fetched=len(items),
        vintages=len({item.knowledge_time for item in items}),
        new_versions=0 if failed else stored,
        duplicates=0 if failed else duplicates,
        new_periods=0 if failed else counts[Kind.NEW_PERIOD],
        revisions=0 if failed else counts[Kind.REVISED],
        unchanged=0 if failed else counts[Kind.UNCHANGED],
        max_abs_revision=None if failed else max_revision,
        raw_sha256=raw_sha256,
    )


def run_ingestion(
    store: AsOfStore,
    chunks: Iterable[Chunk],
    *,
    source: str,
    lineage: LineageLog | None = None,
    archive: RawArchive | None = None,
    clock: Callable[[], datetime] = _now,
) -> list[IngestReport]:
    """Ingest every chunk, archiving raw bytes and logging each report.

    Chunks are processed in order and each is stored atomically. On a failed chunk the report is
    logged first, then ``IngestFailed`` is raised; chunks stored before it stay stored, so a
    corrected re-run continues safely.
    """
    reports: list[IngestReport] = []
    for chunk in chunks:
        raw_sha256 = None
        if archive is not None and chunk.raw is not None:
            raw_sha256 = archive.save(chunk.request, chunk.raw, clock()).sha256
        report = ingest(
            store,
            chunk.observations,
            source=source,
            request=chunk.request,
            raw_sha256=raw_sha256,
            clock=clock,
        )
        if lineage is not None:
            lineage.append(report)
        reports.append(report)
        if report.status == "failed":
            raise IngestFailed(report, reports)
    return reports


def archived_chunks(
    archive: RawArchive, parse: Callable[[bytes], list[Observation]]
) -> Iterator[Chunk]:
    """Replay a raw archive as chunks, without any network access."""
    for entry in archive.entries():
        raw = archive.read(entry)
        yield Chunk(request=entry.request, raw=raw, observations=parse(raw))
