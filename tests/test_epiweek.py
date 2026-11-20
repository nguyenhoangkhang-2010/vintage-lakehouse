"""Tests for MMWR epiweek conversion."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vintage_lakehouse.ingestion.epiweek import epiweek_to_date


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
