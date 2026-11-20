"""Live check against the real Delphi API. Deselected by default; run with: pytest -m network."""

from __future__ import annotations

import pytest

from vintage_lakehouse.ingestion.delphi import probe_fluview


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
