"""Tests for the helpers shared by the baseline forecasters."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import pytest

from series_fixture import START, UTC, history_from_values
from vintage_lakehouse.forecast import (
    InsufficientHistory,
    empirical_quantile,
    regularize,
    steps_ahead,
)
from vintage_lakehouse.forecast.series import check_levels, finish, normal_quantiles
from vintage_lakehouse.storage import Observation


def test_regularize_keeps_values_in_order() -> None:
    series = regularize(history_from_values([1.0, 2.0, 3.0]))
    assert series.values == (1.0, 2.0, 3.0)
    assert series.start == START
    assert series.n == 3
    assert series.last_event == START + timedelta(weeks=2)
    assert series.date_at(5) == START + timedelta(weeks=5)


def test_a_missing_period_is_filled_with_the_value_before_it() -> None:
    history = history_from_values([1.0, 2.0, 3.0, 4.0])
    del history[2]  # the third week is missing
    assert regularize(history).values == (1.0, 2.0, 2.0, 4.0)


def test_an_empty_history_is_insufficient() -> None:
    with pytest.raises(InsufficientHistory, match="empty"):
        regularize([])


def test_unordered_or_duplicate_history_is_rejected() -> None:
    history = history_from_values([1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="ordered by event_time"):
        regularize([history[1], history[0], history[2]])
    with pytest.raises(ValueError, match="ordered by event_time"):
        regularize([history[0], history[0]])


def test_periods_off_the_step_grid_are_rejected() -> None:
    first = history_from_values([1.0])[0]
    odd = Observation(
        "s", START + timedelta(days=10), datetime(2023, 2, 1, tzinfo=UTC), 2.0, "test"
    )
    with pytest.raises(ValueError, match="not a multiple of 7 days"):
        regularize([first, odd])


def test_steps_ahead_counts_whole_steps_after_the_last_period() -> None:
    series = regularize(history_from_values([1.0, 2.0, 3.0]))
    assert steps_ahead(series, series.last_event + timedelta(weeks=1)) == 1
    assert steps_ahead(series, series.last_event + timedelta(weeks=4)) == 4


@pytest.mark.parametrize("days", [0, -7, 3, 10])
def test_steps_ahead_rejects_targets_that_are_not_later_whole_steps(days: int) -> None:
    series = regularize(history_from_values([1.0, 2.0, 3.0]))
    with pytest.raises(ValueError, match="positive whole number"):
        steps_ahead(series, series.last_event + timedelta(days=days))


@pytest.mark.parametrize(
    ("level", "expected"),
    [(0.0, 1.0), (0.25, 1.75), (0.5, 2.5), (0.75, 3.25), (1.0, 4.0), (0.1, 1.3)],
)
def test_empirical_quantile_interpolates_linearly(level: float, expected: float) -> None:
    assert empirical_quantile([1.0, 2.0, 3.0, 4.0], level) == pytest.approx(expected)


def test_empirical_quantile_of_one_value_is_that_value() -> None:
    assert empirical_quantile([7.0], 0.9) == 7.0


def test_empirical_quantile_of_nothing_is_an_error() -> None:
    with pytest.raises(ValueError, match="no values"):
        empirical_quantile([], 0.5)


def test_normal_quantiles_are_symmetric_around_the_mean() -> None:
    quantiles = normal_quantiles(10.0, 2.0, [0.025, 0.5, 0.975])
    assert quantiles[0.5] == pytest.approx(10.0)
    assert quantiles[0.975] - 10.0 == pytest.approx(10.0 - quantiles[0.025])
    assert quantiles[0.975] == pytest.approx(10.0 + 1.959964 * 2.0, rel=1e-5)


def test_a_zero_spread_collapses_onto_the_mean() -> None:
    assert normal_quantiles(3.0, 0.0, [0.25, 0.5, 0.75]) == {0.25: 3.0, 0.5: 3.0, 0.75: 3.0}


@pytest.mark.parametrize(("mean", "sd"), [(math.nan, 1.0), (1.0, math.inf)])
def test_normal_quantiles_need_finite_inputs(mean: float, sd: float) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        normal_quantiles(mean, sd, [0.5])


def test_finish_removes_crossings_and_applies_the_lower_bound() -> None:
    crossed = {0.25: 5.0, 0.5: 3.0, 0.75: 4.0}
    assert finish(crossed, None) == {0.25: 3.0, 0.5: 4.0, 0.75: 5.0}
    assert finish({0.25: -2.0, 0.5: 1.0, 0.75: 4.0}, 0.0) == {0.25: 0.0, 0.5: 1.0, 0.75: 4.0}


def test_check_levels_validates_and_sorts() -> None:
    assert check_levels([0.75, 0.5, 0.25]) == (0.25, 0.5, 0.75)
    with pytest.raises(ValueError, match="symmetric"):
        check_levels([0.5, 0.75])
    with pytest.raises(ValueError, match="median"):
        check_levels([0.25, 0.75])


def test_known_dates_are_not_needed_for_regularisation() -> None:
    history = history_from_values([1.0, 2.0], start=date(2020, 2, 29))
    assert regularize(history).start == date(2020, 2, 29)
