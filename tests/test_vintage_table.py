"""Tests for ``VintageTable``: every version of every period, queried as of a moment."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backfill_fixture import SERIES, UTC, make_store, report_time, revised_series, week
from vintage_lakehouse.backfill import VintageTable


def table(n_weeks: int = 10) -> VintageTable:
    return VintageTable.from_observations(revised_series(n_weeks, [10.0] * n_weeks))


def test_first_release_is_the_first_version() -> None:
    t = table()
    assert t.first_release(week(3)) == report_time(3)
    assert t.first_release(week(99)) is None


def test_value_at_returns_the_latest_version_known_then() -> None:
    t = table()
    # week 3: 8.0 at report 3, 9.0 at report 4, 10.0 at report 5
    assert t.value_at(week(3), report_time(3)) == pytest.approx(8.0)
    assert t.value_at(week(3), report_time(4)) == pytest.approx(9.0)
    assert t.value_at(week(3), report_time(5)) == pytest.approx(10.0)
    assert t.value_at(week(3), report_time(9)) == pytest.approx(10.0)


def test_value_at_is_inclusive_and_none_before_the_first_release() -> None:
    t = table()
    assert t.value_at(week(3), report_time(3) - timedelta(seconds=1)) is None
    assert t.value_at(week(3), report_time(3)) is not None
    assert t.value_at(week(99), report_time(5)) is None


def test_periods_known_lists_periods_released_by_then_in_order() -> None:
    t = table()
    assert t.periods_known(report_time(4)) == [week(i) for i in range(5)]
    assert t.periods_known(report_time(0) - timedelta(days=1)) == []


def test_built_from_a_store_matches_the_observations() -> None:
    observations = revised_series(8, [10.0] * 8)
    from_store = VintageTable.from_store(make_store(observations), SERIES, report_time(20))
    direct = VintageTable.from_observations(observations)
    for w in range(8):
        for k in range(8):
            assert from_store.value_at(week(w), report_time(k)) == direct.value_at(
                week(w), report_time(k)
            )


def test_from_store_ignores_versions_after_the_limit() -> None:
    observations = revised_series(8, [10.0] * 8)
    t = VintageTable.from_store(make_store(observations), SERIES, report_time(4))
    assert t.periods_known(report_time(20)) == [week(i) for i in range(5)]
    # week 3's version at report 5 (the final one) is after the limit and is not in the table
    assert t.value_at(week(3), report_time(20)) == pytest.approx(9.0)


def test_from_store_of_an_unknown_series_is_empty() -> None:
    later = datetime(2030, 1, 1, tzinfo=UTC)
    t = VintageTable.from_store(make_store([]), "nothing", later)
    assert t.periods_known(later) == []


def test_mixed_series_are_rejected() -> None:
    mixed = [*revised_series(3, [1.0] * 3), *revised_series(3, [1.0] * 3, series="other")]
    with pytest.raises(ValueError, match="one series"):
        VintageTable.from_observations(mixed)
