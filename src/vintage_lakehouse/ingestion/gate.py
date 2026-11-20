"""Week-0 data gate: does a source keep enough revision history for ``as_of`` queries?

The criteria are documented in ``docs/DATA_SOURCES.md``. This module holds the pure
evaluation logic (no network access) so it can be tested offline. Network probes live next
to each source, for example ``vintage_lakehouse.ingestion.delphi``.

``VersionedPoint`` is deliberately minimal and local to the gate. The full observation model
arrives in ``feature/time-model``.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import Enum


class Status(str, Enum):
    """Outcome of one gate criterion."""

    PASS = "pass"
    FAIL = "fail"
    MANUAL = "manual"  # cannot be decided by code; a human must check and record it


@dataclass(frozen=True)
class VersionedPoint:
    """One published version of one value."""

    event_time: date  # the period the value describes
    knowledge_time: date  # when the source published this version
    value: float


@dataclass(frozen=True)
class ProbeMeta:
    """What the probe observed about access to the source."""

    requests: int
    elapsed_seconds: float
    auth_required: bool
    throttled: bool


@dataclass(frozen=True)
class GateThresholds:
    """Numeric thresholds for the data criteria."""

    min_versions: int = 3
    min_span_years: float = 3.0
    min_revised_share: float = 0.05


@dataclass(frozen=True)
class GateStats:
    """Summary of the sampled versions."""

    n_event_times: int
    n_rows: int
    min_versions: int
    median_versions: float
    revised_share: float
    first_event: date | None
    last_event: date | None
    span_years: float


@dataclass(frozen=True)
class CriterionResult:
    """Result of one numbered gate criterion."""

    number: int
    name: str
    status: Status
    detail: str


@dataclass(frozen=True)
class GateReport:
    """Full gate result for one source."""

    source: str
    stats: GateStats
    criteria: tuple[CriterionResult, ...]

    @property
    def failures(self) -> tuple[CriterionResult, ...]:
        """Criteria that failed outright."""
        return tuple(c for c in self.criteria if c.status is Status.FAIL)

    @property
    def manual(self) -> tuple[CriterionResult, ...]:
        """Criteria a human still has to check."""
        return tuple(c for c in self.criteria if c.status is Status.MANUAL)

    @property
    def passed(self) -> bool:
        """True when no criterion failed. Manual criteria still need a human decision."""
        return not self.failures


def compute_stats(points: Iterable[VersionedPoint]) -> GateStats:
    """Summarise versions per event time and how often values changed between versions."""
    by_event: dict[date, list[VersionedPoint]] = defaultdict(list)
    n_rows = 0
    for point in points:
        by_event[point.event_time].append(point)
        n_rows += 1
    if not by_event:
        return GateStats(0, 0, 0, 0.0, 0.0, None, None, 0.0)

    version_counts = [len({p.knowledge_time for p in group}) for group in by_event.values()]
    revised = sum(1 for group in by_event.values() if len({p.value for p in group}) > 1)
    first_event = min(by_event)
    last_event = max(by_event)
    return GateStats(
        n_event_times=len(by_event),
        n_rows=n_rows,
        min_versions=min(version_counts),
        median_versions=float(statistics.median(version_counts)),
        revised_share=revised / len(by_event),
        first_event=first_event,
        last_event=last_event,
        span_years=(last_event - first_event).days / 365.25,
    )


def evaluate(
    source: str,
    points: Iterable[VersionedPoint],
    meta: ProbeMeta,
    thresholds: GateThresholds | None = None,
) -> GateReport:
    """Evaluate the five gate criteria for ``source``."""
    limits = thresholds or GateThresholds()
    stats = compute_stats(points)

    has_versions = (
        stats.n_event_times > 0
        and stats.min_versions >= limits.min_versions
        and stats.revised_share >= limits.min_revised_share
    )
    versions_detail = (
        f"{stats.n_event_times} event times; versions each: min {stats.min_versions}, "
        f"median {stats.median_versions:g}; {stats.revised_share:.0%} changed value between "
        f"versions (need min versions >= {limits.min_versions} and "
        f"revised >= {limits.min_revised_share:.0%})"
    )
    span_detail = (
        f"{stats.span_years:.1f} years sampled "
        f"({stats.first_event} to {stats.last_event}; need >= {limits.min_span_years:g})"
    )
    access = (
        CriterionResult(
            3,
            "Reproducible access without paid keys",
            Status.MANUAL,
            "access needed credentials; confirm the key is free and reproducible",
        )
        if meta.auth_required
        else CriterionResult(
            3,
            "Reproducible access without paid keys",
            Status.PASS,
            "anonymous requests succeeded",
        )
    )
    rate_status = Status.FAIL if meta.throttled else Status.PASS
    rate_detail = (
        f"{meta.requests} requests in {meta.elapsed_seconds:.1f}s ({stats.n_rows} rows); "
        + ("throttling observed" if meta.throttled else "no throttling observed")
        + " (sample only; a full historical pull is not tested)"
    )

    criteria = (
        CriterionResult(
            1,
            "Several published versions per event time",
            Status.PASS if has_versions else Status.FAIL,
            versions_detail,
        ),
        CriterionResult(
            2,
            "At least 3 years of history",
            Status.PASS if stats.span_years >= limits.min_span_years else Status.FAIL,
            span_detail,
        ),
        access,
        CriterionResult(
            4,
            "Licence allows the use",
            Status.MANUAL,
            "terms of use cannot be checked by code; verify and record them",
        ),
        CriterionResult(5, "Rate limits allow a full pull", rate_status, rate_detail),
    )
    return GateReport(source=source, stats=stats, criteria=criteria)


def format_markdown(report: GateReport) -> str:
    """Render a report as a Markdown table."""
    lines = [
        f"### Gate report: {report.source}",
        "",
        "| # | Criterion | Status | Detail |",
        "|---|-----------|--------|--------|",
    ]
    for c in report.criteria:
        lines.append(f"| {c.number} | {c.name} | {c.status.value} | {c.detail} |")
    return "\n".join(lines)
