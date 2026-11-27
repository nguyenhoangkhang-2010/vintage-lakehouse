"""Tests for the Delphi FluView ingestion source (offline)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from fluview_fixture import fake_fetch, payload, row, year_response
from vintage_lakehouse.ingestion.delphi import (
    FluViewSource,
    decode_payload,
    parse_observations,
    series_id_for,
)
from vintage_lakehouse.ingestion.errors import FetchError

UTC = timezone.utc


def test_series_id_names_the_region() -> None:
    assert series_id_for("nat") == "delphi/fluview/nat/wili"
    assert FluViewSource(region="hhs1").series_id == "delphi/fluview/hhs1/wili"


def test_observations_use_the_next_day_as_knowledge_time() -> None:
    body = decode_payload(payload([row(202301, "2023-01-13", 4.0)]))
    [observation] = parse_observations(body, series_id="s")
    assert observation.series_id == "s"
    assert observation.event_time == date(2023, 1, 1)
    assert observation.knowledge_time == datetime(2023, 1, 14, tzinfo=UTC)
    assert observation.value == 4.0
    assert observation.source == "delphi-fluview"
    assert observation.ingest_time is None


def test_rows_without_a_value_are_skipped() -> None:
    body = decode_payload(payload([row(202301, "2023-01-13", None), row(202302, "2023-01-20")]))
    assert [o.event_time for o in parse_observations(body, series_id="s")] == [date(2023, 1, 8)]


def test_a_row_missing_a_field_is_a_fetch_error() -> None:
    body = {"result": 1, "epidata": [{"epiweek": 202301, "wili": 4.0}]}
    with pytest.raises(FetchError, match="malformed row"):
        parse_observations(body, series_id="s")


def test_a_row_that_cannot_be_stored_is_a_fetch_error() -> None:
    body = decode_payload(payload([row(202301, "2023-01-13", float("nan"))]))
    with pytest.raises(FetchError, match="cannot be stored"):
        parse_observations(body, series_id="s")


def test_a_release_before_the_week_it_describes_is_rejected() -> None:
    body = decode_payload(payload([row(202301, "2022-12-01")]))
    with pytest.raises(FetchError, match="cannot be stored"):
        parse_observations(body, series_id="s")


def test_the_retirement_notice_is_surfaced_as_an_error() -> None:
    message = "this API no longer provides data after 2026-09-22; it has been retired"
    with pytest.raises(FetchError, match="retired"):
        parse_observations(decode_payload(payload([], result=-3, message=message)), series_id="s")


def test_invalid_json_is_a_fetch_error() -> None:
    with pytest.raises(FetchError, match="not valid JSON"):
        decode_payload(b"<html>nope</html>")
    with pytest.raises(FetchError, match="not valid JSON"):
        decode_payload(b"\xff\xfe")


def test_request_urls_cover_the_whole_year_and_all_later_issues() -> None:
    source = FluViewSource(region="nat")
    assert source.request_url(2023, 202638) == (
        "https://api.delphi.cmu.edu/epidata/fluview/"
        "?regions=nat&epiweeks=202301-202352&issues=202301-202638"
    )


def test_a_53_week_year_asks_for_week_53() -> None:
    assert "epiweeks=202001-202053" in FluViewSource().request_url(2020, 202638)


def test_chunks_come_one_year_at_a_time_with_a_pause_between() -> None:
    pauses: list[float] = []
    source = FluViewSource(fetch=fake_fetch(), pause_seconds=0.25, sleep=pauses.append)
    chunks = list(source.chunks(2021, 2023, last_issue=202652))
    assert [c.request.split("epiweeks=")[1][:6] for c in chunks] == ["202101", "202201", "202301"]
    assert pauses == [0.25, 0.25]
    assert all(len(c.observations) == 12 for c in chunks)
    assert all(c.raw == year_response(int(c.request.split("epiweeks=")[1][:4])) for c in chunks)


def test_years_that_start_after_the_last_issue_are_not_requested() -> None:
    requested: list[str] = []

    def fetch(url: str) -> bytes:
        requested.append(url)
        return payload([])

    source = FluViewSource(fetch=fetch, pause_seconds=0, sleep=lambda _: None)
    assert len(list(source.chunks(2023, 2026, last_issue=202401))) == 2
    assert len(requested) == 2


def test_no_years_means_no_requests() -> None:
    def fetch(url: str) -> bytes:
        raise AssertionError("no request expected")

    source = FluViewSource(fetch=fetch)
    assert list(source.chunks(2024, 2023, last_issue=202652)) == []


def test_a_fetch_failure_stops_the_iteration() -> None:
    def fetch(url: str) -> bytes:
        raise FetchError("down", status=503)

    source = FluViewSource(fetch=fetch)
    with pytest.raises(FetchError, match="down"):
        list(source.chunks(2023, 2023, last_issue=202652))


def test_parse_matches_parse_observations() -> None:
    source = FluViewSource()
    raw = year_response(2023)
    assert source.parse(raw) == parse_observations(decode_payload(raw), series_id=source.series_id)
