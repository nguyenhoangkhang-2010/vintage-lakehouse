"""Tests for the bitemporal Observation model."""

from __future__ import annotations

import dataclasses
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vintage_lakehouse.storage import Observation, ObservationKey, knowledge_time_from_date

UTC = timezone.utc
VIETNAM = timezone(timedelta(hours=7))


def make(**overrides: Any) -> Observation:
    fields: dict[str, Any] = {
        "series_id": "fluview/nat/wili",
        "event_time": date(2023, 1, 1),
        "knowledge_time": datetime(2023, 1, 13, 12, 0, tzinfo=UTC),
        "value": 4.0,
        "source": "delphi-fluview",
    }
    fields.update(overrides)
    return Observation(**fields)


def test_valid_observation_keeps_its_fields() -> None:
    obs = make()
    assert obs.series_id == "fluview/nat/wili"
    assert obs.event_time == date(2023, 1, 1)
    assert obs.knowledge_time == datetime(2023, 1, 13, 12, 0, tzinfo=UTC)
    assert obs.value == 4.0
    assert obs.source == "delphi-fluview"
    assert obs.ingest_time is None


def test_knowledge_time_is_normalised_to_utc() -> None:
    local = datetime(2023, 1, 13, 19, 0, tzinfo=VIETNAM)
    obs = make(knowledge_time=local)
    assert obs.knowledge_time.utcoffset() == timedelta(0)
    assert obs.knowledge_time == datetime(2023, 1, 13, 12, 0, tzinfo=UTC)


def test_ingest_time_is_optional_and_normalised() -> None:
    obs = make(ingest_time=datetime(2024, 5, 1, 7, 0, tzinfo=VIETNAM))
    assert obs.ingest_time == datetime(2024, 5, 1, 0, 0, tzinfo=UTC)
    assert obs.ingest_time is not None
    assert obs.ingest_time.utcoffset() == timedelta(0)


def test_naive_knowledge_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="knowledge_time must be timezone-aware"):
        make(knowledge_time=datetime(2023, 1, 13, 12, 0))


def test_naive_ingest_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="ingest_time must be timezone-aware"):
        make(ingest_time=datetime(2024, 5, 1, 0, 0))


def test_knowledge_time_must_be_a_datetime() -> None:
    with pytest.raises(TypeError, match="knowledge_time must be a datetime"):
        make(knowledge_time=date(2023, 1, 13))


@pytest.mark.parametrize("bad", [datetime(2023, 1, 1, tzinfo=UTC), "2023-01-01", 202301])
def test_event_time_must_be_a_plain_date(bad: object) -> None:
    with pytest.raises(TypeError, match="event_time must be a date"):
        make(event_time=bad)


def test_knowledge_before_the_period_it_describes_is_rejected() -> None:
    with pytest.raises(ValueError, match="before the period it describes"):
        make(knowledge_time=datetime(2022, 12, 31, 23, 59, tzinfo=UTC))


def test_knowledge_on_the_first_day_of_the_period_is_allowed() -> None:
    obs = make(knowledge_time=datetime(2023, 1, 1, 0, 0, tzinfo=UTC))
    assert obs.knowledge_time.date() == obs.event_time


def test_the_period_check_uses_the_utc_date() -> None:
    # 2023-01-01 05:00 +07:00 is 2022-12-31 22:00 UTC, which is before the period starts.
    with pytest.raises(ValueError, match="before the period it describes"):
        make(knowledge_time=datetime(2023, 1, 1, 5, 0, tzinfo=VIETNAM))


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_values_are_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        make(value=bad)


@pytest.mark.parametrize("bad", [True, "4.0", None])
def test_non_numeric_values_are_rejected(bad: object) -> None:
    with pytest.raises(TypeError, match="value must be a real number"):
        make(value=bad)


def test_integer_values_become_floats() -> None:
    obs = make(value=7)
    assert obs.value == 7.0
    assert isinstance(obs.value, float)


@pytest.mark.parametrize("field", ["series_id", "source"])
@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_text_fields_are_rejected(field: str, bad: str) -> None:
    with pytest.raises(ValueError, match=f"{field} must not be empty"):
        make(**{field: bad})


@pytest.mark.parametrize("field", ["series_id", "source"])
def test_text_fields_must_be_strings(field: str) -> None:
    with pytest.raises(TypeError, match=f"{field} must be a string"):
        make(**{field: 5})


def test_observations_are_immutable() -> None:
    obs = make()
    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.value = 5.0  # type: ignore[misc]


def test_equal_fields_make_equal_observations() -> None:
    assert make() == make()
    assert make() != make(value=4.5)


def test_key_identifies_the_version() -> None:
    obs = make()
    assert obs.key == ObservationKey(
        "fluview/nat/wili", date(2023, 1, 1), datetime(2023, 1, 13, 12, 0, tzinfo=UTC)
    )
    later = make(knowledge_time=datetime(2023, 1, 20, 12, 0, tzinfo=UTC))
    assert obs.key != later.key


def test_date_only_publication_is_known_from_the_next_day() -> None:
    known = knowledge_time_from_date(date(2023, 1, 13))
    assert known == datetime(2023, 1, 14, 0, 0, tzinfo=UTC)
    assert datetime(2023, 1, 13, 23, 59, 59, 999999, tzinfo=UTC) < known


def test_date_only_conversion_rejects_datetimes_and_text() -> None:
    with pytest.raises(TypeError, match="published must be a date"):
        knowledge_time_from_date(datetime(2023, 1, 13, tzinfo=UTC))
    with pytest.raises(TypeError, match="published must be a date"):
        knowledge_time_from_date("2023-01-13")  # type: ignore[arg-type]


def test_converted_date_works_as_knowledge_time() -> None:
    obs = make(knowledge_time=knowledge_time_from_date(date(2023, 1, 13)))
    assert obs.knowledge_time == datetime(2023, 1, 14, 0, 0, tzinfo=UTC)


@given(
    event=st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 1, 1)),
    days=st.integers(min_value=-5, max_value=400),
    offset_hours=st.integers(min_value=-12, max_value=14),
    value=st.floats(allow_nan=True, allow_infinity=True),
)
def test_construction_is_total_and_keeps_the_invariants(
    event: date, days: int, offset_hours: int, value: float
) -> None:
    moment = datetime.combine(
        event + timedelta(days=days),
        time(12),
        tzinfo=timezone(timedelta(hours=offset_hours)),
    )
    try:
        obs = make(event_time=event, knowledge_time=moment, value=value)
    except ValueError:
        return  # a rejected input is fine; any other exception type would fail the test
    assert obs.knowledge_time.utcoffset() == timedelta(0)
    assert obs.knowledge_time == moment
    assert obs.knowledge_time.date() >= obs.event_time
    assert math.isfinite(obs.value)


@given(published=st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 1, 1)))
def test_date_conversion_never_precedes_any_moment_of_that_day(published: date) -> None:
    known = knowledge_time_from_date(published)
    last_moment = datetime.combine(published, time.max, tzinfo=UTC)
    assert known > last_moment
    assert known.utcoffset() == timedelta(0)
