"""Live check against the real Delphi API. Deselected by default; run with: pytest -m network."""

from __future__ import annotations

import pytest

from vintage_lakehouse.ingestion.delphi import FluViewSource, probe_fluview
from vintage_lakehouse.ingestion.pipeline import run_ingestion
from vintage_lakehouse.storage import InMemoryStore


@pytest.mark.network
def test_live_ingestion_of_one_year_finds_revisions() -> None:
    source = FluViewSource()
    store = InMemoryStore()
    reports = run_ingestion(
        store, source.chunks(2023, 2023, last_issue=202310), source="delphi-fluview"
    )
    [report] = reports
    # Only weeks 1 to 10 of 2023 had been published by issue 202310.
    assert report.new_periods == 10
    assert report.revisions > 0
    assert report.status == "ok"


@pytest.mark.network
def test_live_fluview_returns_several_versions_per_week() -> None:
    points, meta = probe_fluview(years=[2023], pause_seconds=0.5)
    assert not meta.throttled
    assert not meta.auth_required
    versions_by_week: dict[object, set[object]] = {}
    for point in points:
        versions_by_week.setdefault(point.event_time, set()).add(point.knowledge_time)
    assert versions_by_week
    assert all(len(v) >= 3 for v in versions_by_week.values())
