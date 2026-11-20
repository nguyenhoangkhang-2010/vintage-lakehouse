"""Tests for the Delphi FluView probe (offline, with an injected fetcher)."""

from __future__ import annotations

import itertools
import re
from datetime import date, timedelta

import pytest

from vintage_lakehouse.ingestion.delphi import (
    SOURCE_ID,
    build_url,
    parse_fluview,
    probe_fluview,
    run_gate,
    sample_epiweeks,
)
from vintage_lakehouse.ingestion.epiweek import epiweek_to_date
from vintage_lakehouse.ingestion.errors import FetchError


def row(epiweek: int, release: str, wili: float | None) -> dict[str, object]:
    return {"release_date": release, "region": "nat", "epiweek": epiweek, "wili": wili}


def fake_fluview(url: str) -> object:
    """Return five weekly issues with a drifting value for the requested epiweek."""
    epiweek = int(re.search(r"epiweeks=(\d+)", url).group(1))  # type: ignore[union-attr]
    start = epiweek_to_date(epiweek)
    rows = [
        row(epiweek, (start + timedelta(days=7 * (k + 1))).isoformat(), 1.0 + 0.1 * k)
        for k in range(5)
    ]
    return {"result": 1, "message": "success", "epidata": rows}


def test_parse_converts_epiweek_and_release_date() -> None:
    points = parse_fluview({"result": 1, "epidata": [row(202301, "2023-01-13", 4.0)]})
    assert len(points) == 1
    assert points[0].event_time == date(2023, 1, 1)
    assert points[0].knowledge_time == date(2023, 1, 13)
    assert points[0].value == 4.0


def test_parse_skips_rows_without_a_value() -> None:
    payload = {"result": 1, "epidata": [row(202301, "2023-01-13", None)]}
    assert parse_fluview(payload) == []


def test_parse_treats_no_results_as_empty() -> None:
    assert parse_fluview({"result": -2, "message": "no results"}) == []


def test_parse_raises_on_api_error() -> None:
    with pytest.raises(FetchError, match="API error"):
        parse_fluview({"result": -1, "message": "bad request"})


def test_parse_rejects_non_object() -> None:
    with pytest.raises(FetchError, match="not a JSON object"):
        parse_fluview([1, 2, 3])


def test_build_url_asks_for_one_week_across_its_issues() -> None:
    assert build_url(202305) == (
        "https://api.delphi.cmu.edu/epidata/fluview/"
        "?regions=nat&epiweeks=202305&issues=202305-202325"
    )


@pytest.mark.parametrize(("epiweek", "ahead"), [(202545, 20), (202305, 0), (202305, 21)])
def test_build_url_rejects_unsafe_ranges(epiweek: int, ahead: int) -> None:
    with pytest.raises(ValueError, match=r"issues_ahead|year boundary"):
        build_url(epiweek, issues_ahead=ahead)


def test_sample_epiweeks_takes_two_weeks_per_year() -> None:
    assert sample_epiweeks([2019, 2020]) == [201905, 201925, 202005, 202025]


def test_probe_collects_points_and_counts_requests() -> None:
    pauses: list[float] = []
    ticks = itertools.count()
    points, meta = probe_fluview(
        years=[2019, 2020],
        fetch=fake_fluview,
        pause_seconds=0.25,
        clock=lambda: float(next(ticks)),
        sleep=pauses.append,
    )
    assert len(points) == 4 * 5
    assert meta.requests == 4
    assert pauses == [0.25, 0.25, 0.25]
    assert meta.elapsed_seconds > 0
    assert not meta.auth_required
    assert not meta.throttled


def test_probe_stops_and_flags_throttling() -> None:
    calls = 0

    def fetch(url: str) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise FetchError("slow down", status=429)
        return fake_fluview(url)

    points, meta = probe_fluview(
        years=[2019, 2020], fetch=fetch, pause_seconds=0, sleep=lambda _: None
    )
    assert meta.throttled
    assert meta.requests == 2
    assert len(points) == 5


def test_probe_flags_missing_credentials() -> None:
    def fetch(url: str) -> object:
        raise FetchError("forbidden", status=403)

    points, meta = probe_fluview(years=[2019], fetch=fetch, pause_seconds=0, sleep=lambda _: None)
    assert meta.auth_required
    assert points == []


def test_probe_propagates_other_failures() -> None:
    def fetch(url: str) -> object:
        raise FetchError("server error", status=500)

    with pytest.raises(FetchError, match="server error"):
        probe_fluview(years=[2019], fetch=fetch, pause_seconds=0, sleep=lambda _: None)


def test_run_gate_passes_on_well_behaved_source() -> None:
    report = run_gate(fetch=fake_fluview, pause_seconds=0)
    assert report.source == SOURCE_ID
    assert report.passed
    assert report.stats.n_event_times == 14
