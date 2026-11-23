"""Tests for the ingestion pipeline: revision detection, idempotence, atomicity, lineage."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from fluview_fixture import fake_fetch
from vintage_lakehouse.ingestion.archive import RawArchive
from vintage_lakehouse.ingestion.delphi import SOURCE_ID, FluViewSource
from vintage_lakehouse.ingestion.lineage import LineageLog
from vintage_lakehouse.ingestion.pipeline import (
    Chunk,
    IngestFailed,
    Kind,
    archived_chunks,
    classify,
    ingest,
    run_ingestion,
)
from vintage_lakehouse.storage import InMemoryStore, Observation

UTC = timezone.utc
EVENT = date(2023, 1, 1)
RUN_START = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


def obs(
    day: int,
    value: float,
    *,
    event: date = EVENT,
    series: str = "s",
    source: str = "src",
    ingest_time: datetime | None = None,
) -> Observation:
    return Observation(
        series_id=series,
        event_time=event,
        knowledge_time=datetime(2023, 2, day, tzinfo=UTC),
        value=value,
        source=source,
        ingest_time=ingest_time,
    )


class Clock:
    """A clock that returns increasing times, one minute apart."""

    def __init__(self) -> None:
        self.now = RUN_START

    def __call__(self) -> datetime:
        current = self.now
        self.now += timedelta(minutes=1)
        return current


def run(store: InMemoryStore, items: list[Observation], clock: Clock | None = None) -> Any:
    return ingest(store, items, source="src", request="req", clock=clock or Clock())


# --- classification ----------------------------------------------------------------------


def test_the_first_version_of_a_period_is_new() -> None:
    assert classify(None, obs(1, 4.0)) is Kind.NEW_PERIOD


def test_a_different_value_is_a_revision() -> None:
    assert classify(obs(1, 4.0), obs(8, 4.5)) is Kind.REVISED


def test_the_same_value_republished_is_unchanged() -> None:
    assert classify(obs(1, 4.0), obs(8, 4.0)) is Kind.UNCHANGED


# --- ingest ------------------------------------------------------------------------------


def test_counts_new_periods_revisions_and_unchanged_versions() -> None:
    store = InMemoryStore()
    report = run(store, [obs(1, 4.0), obs(8, 4.1), obs(15, 4.1)])
    assert report.status == "ok"
    assert report.error is None
    assert report.rows_fetched == 3
    assert report.vintages == 3
    assert report.new_versions == 3
    assert report.new_periods == 1
    assert report.revisions == 1
    assert report.unchanged == 1
    assert report.duplicates == 0
    assert report.max_abs_revision == pytest.approx(0.1)


def test_the_order_in_which_versions_arrive_does_not_change_the_counts() -> None:
    items = [obs(1, 4.0), obs(8, 4.1), obs(15, 4.1)]
    forward = run(InMemoryStore(), items)
    backward = run(InMemoryStore(), list(reversed(items)))
    for field in ("new_periods", "revisions", "unchanged", "new_versions"):
        assert getattr(forward, field) == getattr(backward, field)


def test_reingesting_the_same_chunk_stores_nothing() -> None:
    store = InMemoryStore()
    items = [obs(1, 4.0), obs(8, 4.1), obs(15, 4.1)]
    run(store, items)
    before = store.revisions("s", EVENT)

    again = run(store, items)
    assert again.status == "ok"
    assert again.new_versions == 0
    assert again.duplicates == 3
    assert (again.new_periods, again.revisions, again.unchanged) == (0, 0, 0)
    assert again.max_abs_revision is None
    assert store.revisions("s", EVENT) == before


def test_the_first_load_time_is_kept_when_data_is_loaded_again() -> None:
    store = InMemoryStore()
    clock = Clock()
    run(store, [obs(1, 4.0)], clock)
    first_load = RUN_START
    run(store, [obs(1, 4.0)], clock)
    [stored] = store.revisions("s", EVENT)
    assert stored.ingest_time == first_load


def test_new_versions_are_stamped_with_the_start_of_the_run() -> None:
    store = InMemoryStore()
    run(store, [obs(1, 4.0), obs(8, 4.1)])
    assert [v.ingest_time for v in store.revisions("s", EVENT)] == [RUN_START, RUN_START]


def test_an_ingest_time_given_by_the_source_is_kept() -> None:
    store = InMemoryStore()
    given = datetime(2020, 5, 5, tzinfo=UTC)
    run(store, [obs(1, 4.0, ingest_time=given)])
    assert store.revisions("s", EVENT)[0].ingest_time == given


def test_a_later_chunk_is_classified_against_what_is_already_stored() -> None:
    store = InMemoryStore()
    run(store, [obs(1, 4.0)])
    report = run(store, [obs(8, 4.5)])
    assert (report.new_periods, report.revisions, report.unchanged) == (0, 1, 0)
    assert report.max_abs_revision == pytest.approx(0.5)


def test_a_backfilled_version_is_compared_with_its_own_predecessor() -> None:
    store = InMemoryStore()
    run(store, [obs(1, 4.0), obs(22, 9.0)])
    # Published between the two, with the earlier value: unchanged relative to what came
    # before it, even though it differs from the later 9.0.
    report = run(store, [obs(8, 4.0)])
    assert (report.new_periods, report.revisions, report.unchanged) == (0, 0, 1)


def test_periods_and_series_are_tracked_independently() -> None:
    store = InMemoryStore()
    other_week = date(2023, 1, 8)
    report = run(
        store,
        [
            obs(1, 4.0),
            obs(8, 5.0),
            obs(1, 1.0, event=other_week),
            obs(1, 7.0, series="t"),
            obs(8, 7.0, series="t"),
        ],
    )
    assert report.new_periods == 3
    assert report.revisions == 1
    assert report.unchanged == 1
    assert report.new_versions == 5


def test_the_largest_revision_is_reported() -> None:
    report = run(InMemoryStore(), [obs(1, 4.0), obs(8, 4.5), obs(15, 3.0)])
    assert report.max_abs_revision == pytest.approx(1.5)


def test_a_conflicting_version_fails_the_chunk_and_stores_nothing_from_it() -> None:
    store = InMemoryStore()
    run(store, [obs(1, 4.0)])
    report = run(store, [obs(8, 5.0), obs(1, 9.9)])
    assert report.status == "failed"
    assert report.error is not None
    assert "already stored" in report.error
    assert report.rows_fetched == 2
    assert (report.new_versions, report.new_periods, report.revisions) == (0, 0, 0)
    assert [v.value for v in store.revisions("s", EVENT)] == [4.0]


def test_a_conflict_inside_one_chunk_fails_it() -> None:
    store = InMemoryStore()
    report = run(store, [obs(1, 4.0), obs(1, 5.0)])
    assert report.status == "failed"
    assert store.revisions("s", EVENT) == []


def test_non_observations_are_rejected() -> None:
    bad: list[Any] = [obs(1, 4.0), "nope"]
    with pytest.raises(TypeError, match="expected an Observation"):
        run(InMemoryStore(), bad)


def test_an_empty_chunk_is_fine() -> None:
    report = run(InMemoryStore(), [])
    assert report.status == "ok"
    assert (report.rows_fetched, report.new_versions, report.vintages) == (0, 0, 0)


def test_the_run_id_and_timing_are_recorded() -> None:
    report = ingest(
        InMemoryStore(), [obs(1, 4.0)], source="src", request="req", run_id="fixed", clock=Clock()
    )
    assert report.run_id == "fixed"
    assert report.started_at == RUN_START
    assert report.finished_at > report.started_at
    assert report.source == "src"
    assert report.request == "req"


def test_a_run_id_is_generated_when_none_is_given() -> None:
    first = run(InMemoryStore(), [obs(1, 4.0)])
    second = run(InMemoryStore(), [obs(1, 4.0)])
    assert first.run_id != second.run_id


# --- run_ingestion -----------------------------------------------------------------------


def chunk(request: str, items: list[Observation], raw: bytes | None = b"raw") -> Chunk:
    return Chunk(request=request, raw=raw, observations=items)


def test_run_ingestion_logs_and_archives_every_chunk(tmp_path: Path) -> None:
    store = InMemoryStore()
    lineage = LineageLog(tmp_path / "lineage.jsonl")
    archive = RawArchive(tmp_path / "raw", "src")
    reports = run_ingestion(
        store,
        [chunk("req1", [obs(1, 4.0)], b"one"), chunk("req2", [obs(8, 4.5)], b"two")],
        source="src",
        lineage=lineage,
        archive=archive,
        clock=Clock(),
    )
    assert [r.request for r in reports] == ["req1", "req2"]
    assert lineage.read() == reports
    assert [e.sha256 for e in archive.entries()] == [r.raw_sha256 for r in reports]
    assert reports[1].revisions == 1


def test_run_ingestion_works_without_lineage_or_archive() -> None:
    store = InMemoryStore()
    [report] = run_ingestion(store, [chunk("req", [obs(1, 4.0)])], source="src")
    assert report.raw_sha256 is None
    assert len(store.revisions("s", EVENT)) == 1


def test_chunks_without_raw_bytes_are_not_archived(tmp_path: Path) -> None:
    archive = RawArchive(tmp_path / "raw", "src")
    [report] = run_ingestion(
        InMemoryStore(), [chunk("req", [obs(1, 4.0)], raw=None)], source="src", archive=archive
    )
    assert report.raw_sha256 is None
    assert archive.entries() == []


def test_a_failed_chunk_is_logged_then_raised_and_later_chunks_are_not_read(
    tmp_path: Path,
) -> None:
    store = InMemoryStore()
    run(store, [obs(1, 4.0)])
    lineage = LineageLog(tmp_path / "lineage.jsonl")
    consumed: list[str] = []

    def chunks() -> Iterator[Chunk]:
        for name, items in [
            ("good", [obs(8, 4.5)]),
            ("bad", [obs(1, 9.9)]),
            ("never", [obs(15, 1.0)]),
        ]:
            consumed.append(name)
            yield chunk(name, items)

    with pytest.raises(IngestFailed) as caught:
        run_ingestion(store, chunks(), source="src", lineage=lineage)

    assert consumed == ["good", "bad"]
    assert caught.value.report.request == "bad"
    assert [r.status for r in caught.value.reports] == ["ok", "failed"]
    assert [r.status for r in lineage.read()] == ["ok", "failed"]
    assert [v.value for v in store.revisions("s", EVENT)] == [4.0, 4.5]


# --- replaying the archive ---------------------------------------------------------------


def test_replaying_the_archive_rebuilds_the_same_store_without_the_network(
    tmp_path: Path,
) -> None:
    source = FluViewSource(fetch=fake_fetch(), pause_seconds=0, sleep=lambda _: None)
    archive = RawArchive(tmp_path / "raw", SOURCE_ID)
    live = InMemoryStore()
    run_ingestion(
        live,
        source.chunks(2022, 2023, last_issue=202652),
        source=SOURCE_ID,
        archive=archive,
        clock=Clock(),
    )

    def no_network(url: str) -> bytes:
        raise AssertionError(f"the network must not be used: {url}")

    offline = FluViewSource(fetch=no_network)
    replayed = InMemoryStore()
    run_ingestion(
        replayed, archived_chunks(archive, offline.parse), source=SOURCE_ID, clock=Clock()
    )

    def content(versions: list[Observation]) -> list[tuple[object, float, str]]:
        # ingest_time records when each run happened, so it is left out of the comparison.
        return [(v.key, v.value, v.source) for v in versions]

    at = datetime(2030, 1, 1, tzinfo=UTC)
    assert content(replayed.as_of(source.series_id, at)) == content(
        live.as_of(source.series_id, at)
    )
    assert len(live.as_of(source.series_id, at)) == 6
    for week_start in (date(2022, 1, 2), date(2023, 1, 1)):
        assert content(replayed.revisions(source.series_id, week_start)) == content(
            live.revisions(source.series_id, week_start)
        )
        assert len(live.revisions(source.series_id, week_start)) == 4
