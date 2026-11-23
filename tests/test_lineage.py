"""Tests for the lineage log."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

from vintage_lakehouse.ingestion.lineage import IngestReport, LineageLog

UTC = timezone.utc


def report(**overrides: object) -> IngestReport:
    fields: dict[str, object] = {
        "run_id": "abc123",
        "source": "delphi-fluview",
        "request": "http://example/?epiweeks=202301-202352",
        "started_at": datetime(2026, 11, 23, 9, 0, 0, 123456, tzinfo=UTC),
        "finished_at": datetime(2026, 11, 23, 9, 0, 3, tzinfo=UTC),
        "status": "ok",
        "error": None,
        "rows_fetched": 12,
        "vintages": 9,
        "new_versions": 12,
        "duplicates": 0,
        "new_periods": 3,
        "revisions": 6,
        "unchanged": 3,
        "max_abs_revision": 0.1,
        "raw_sha256": "f" * 64,
    }
    fields.update(overrides)
    return IngestReport(**fields)  # type: ignore[arg-type]


def test_report_round_trips_through_a_dict() -> None:
    original = report()
    assert IngestReport.from_dict(original.to_dict()) == original


def test_report_dict_is_json_friendly() -> None:
    data = report().to_dict()
    assert data["started_at"] == "2026-11-23T09:00:00.123456+00:00"
    assert data["max_abs_revision"] == 0.1


def test_reports_can_carry_missing_fields() -> None:
    sparse = report(max_abs_revision=None, raw_sha256=None)
    assert IngestReport.from_dict(sparse.to_dict()) == sparse


def test_a_missing_log_reads_as_empty(tmp_path: Path) -> None:
    assert LineageLog(tmp_path / "nothing" / "log.jsonl").read() == []


def test_the_log_appends_in_order_and_creates_folders(tmp_path: Path) -> None:
    log = LineageLog(tmp_path / "lineage" / "ingestion.jsonl")
    first, second = report(run_id="one"), report(run_id="two")
    log.append(first)
    log.append(second)
    assert log.read() == [first, second]
    assert len((tmp_path / "lineage" / "ingestion.jsonl").read_text().splitlines()) == 2


def test_failed_reports_keep_their_error(tmp_path: Path) -> None:
    log = LineageLog(tmp_path / "log.jsonl")
    failed = dataclasses.replace(report(), status="failed", error="conflict", new_versions=0)
    log.append(failed)
    [read] = log.read()
    assert read.status == "failed"
    assert read.error == "conflict"


def test_blank_lines_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    log = LineageLog(path)
    log.append(report())
    path.write_text(path.read_text() + "\n\n", encoding="utf-8")
    assert len(log.read()) == 1
