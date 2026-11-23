"""Tests for MMWR epiweek conversion."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vintage_lakehouse.ingestion.epiweek import date_to_epiweek, epiweek_to_date, weeks_in_year


@pytest.mark.parametrize(
    ("year", "weeks"), [(2014, 53), (2015, 52), (2019, 52), (2020, 53), (2021, 52), (2023, 52)]
)
def test_weeks_in_year(year: int, weeks: int) -> None:
    assert weeks_in_year(year) == weeks


@pytest.mark.parametrize(
    ("day", "epiweek"),
    [
        (date(2023, 1, 1), 202301),
        (date(2023, 1, 7), 202301),
        (date(2023, 1, 8), 202302),
        (date(2021, 1, 1), 202053),  # belongs to the last week of the previous epi-year
        (date(2020, 12, 27), 202053),
        (date(2019, 12, 29), 202001),  # belongs to the first week of the next epi-year
        (date(2026, 11, 23), 202647),
    ],
)
def test_date_to_epiweek_known_dates(day: date, epiweek: int) -> None:
    assert date_to_epiweek(day) == epiweek


@given(
    year=st.integers(min_value=2000, max_value=2040),
    week=st.integers(min_value=1, max_value=52),
    offset=st.integers(min_value=0, max_value=6),
)
def test_every_day_of_a_week_maps_back_to_that_week(year: int, week: int, offset: int) -> None:
    epiweek = year * 100 + week
    assert date_to_epiweek(epiweek_to_date(epiweek) + timedelta(days=offset)) == epiweek


def test_known_week_starts() -> None:
    assert epiweek_to_date(202301) == date(2023, 1, 1)
    assert epiweek_to_date(202101) == date(2021, 1, 3)
    assert epiweek_to_date(202001) == date(2019, 12, 29)


def test_week_53_exists_only_in_long_years() -> None:
    assert epiweek_to_date(202053) == date(2020, 12, 27)
    with pytest.raises(ValueError, match="does not exist"):
        epiweek_to_date(202153)


@pytest.mark.parametrize("epiweek", [202300, 202354, 202399])
def test_invalid_week_numbers_are_rejected(epiweek: int) -> None:
    with pytest.raises(ValueError, match="invalid epiweek"):
        epiweek_to_date(epiweek)


@given(
    year=st.integers(min_value=2000, max_value=2040), week=st.integers(min_value=1, max_value=51)
)
def test_weeks_start_on_sunday_and_are_consecutive(year: int, week: int) -> None:
    current = epiweek_to_date(year * 100 + week)
    following = epiweek_to_date(year * 100 + week + 1)
    assert current.weekday() == 6
    assert following - current == timedelta(days=7)
