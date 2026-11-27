"""Tests for forecast scoring (WIS, absolute error, coverage)."""

from __future__ import annotations

import math
from collections.abc import Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vintage_lakehouse.replay import (
    absolute_error,
    central_coverage,
    normalise_quantiles,
    weighted_interval_score,
)

FORECAST = {0.25: 2.0, 0.5: 3.0, 0.75: 6.0}


def test_wis_when_the_observation_is_above_the_interval() -> None:
    # interval score (6-2) + 4*(7-6) = 8, weighted 0.25 -> 2; median 0.5*|7-3| = 2; (2+2)/1.5
    assert weighted_interval_score(FORECAST, 7.0) == pytest.approx(8.0 / 3.0)


def test_wis_when_the_observation_is_inside_the_interval() -> None:
    # interval score 4, weighted 1; median 0.5*|4-3| = 0.5; (1+0.5)/1.5
    assert weighted_interval_score(FORECAST, 4.0) == pytest.approx(1.0)


def test_wis_when_the_observation_is_below_the_interval() -> None:
    # interval score 4 + 4*(2-1) = 8, weighted 2; median 0.5*|1-3| = 1; (2+1)/1.5
    assert weighted_interval_score(FORECAST, 1.0) == pytest.approx(2.0)


def test_wis_with_only_the_median_is_the_absolute_error() -> None:
    assert weighted_interval_score({0.5: 3.0}, 5.0) == pytest.approx(2.0)
    assert weighted_interval_score({0.5: 3.0}, 1.0) == pytest.approx(2.0)


def test_a_perfect_point_forecast_scores_zero() -> None:
    assert weighted_interval_score({0.25: 4.0, 0.5: 4.0, 0.75: 4.0}, 4.0) == 0.0


def test_a_wider_interval_costs_more_when_the_observation_is_inside() -> None:
    narrow = weighted_interval_score({0.25: 3.5, 0.5: 4.0, 0.75: 4.5}, 4.0)
    wide = weighted_interval_score({0.25: 1.0, 0.5: 4.0, 0.75: 7.0}, 4.0)
    assert narrow < wide


def test_absolute_error_uses_the_median() -> None:
    assert absolute_error(FORECAST, 7.0) == 4.0
    assert absolute_error(FORECAST, 1.0) == 2.0


def test_coverage_reports_each_central_interval() -> None:
    forecast = {0.1: 1.0, 0.25: 2.0, 0.5: 3.0, 0.75: 6.0, 0.9: 8.0}
    assert central_coverage(forecast, 7.0) == {0.5: False, 0.8: True}
    assert central_coverage(forecast, 4.0) == {0.5: True, 0.8: True}
    assert central_coverage(forecast, 0.5) == {0.5: False, 0.8: False}
    assert central_coverage({0.5: 3.0}, 3.0) == {}


def test_coverage_includes_values_exactly_on_the_interval_ends() -> None:
    assert central_coverage(FORECAST, 2.0) == {0.5: True}
    assert central_coverage(FORECAST, 6.0) == {0.5: True}
    assert central_coverage(FORECAST, 6.000001) == {0.5: False}


def test_integer_values_are_accepted() -> None:
    assert normalise_quantiles({0.25: 1, 0.5: 2, 0.75: 3}) == [(0.25, 1.0), (0.5, 2.0), (0.75, 3.0)]


def test_levels_that_differ_only_by_float_noise_are_treated_as_partners() -> None:
    forecast = {0.025: 1.0, 0.5: 2.0, 1.0 - 0.025: 3.0}
    assert weighted_interval_score(forecast, 2.0) > 0.0


@pytest.mark.parametrize(
    ("forecast", "message"),
    [
        ({}, "median"),
        ({0.25: 1.0, 0.75: 2.0}, "median"),
        ({0.25: 1.0, 0.5: 2.0}, "no symmetric partner"),
        ({0.0: 1.0, 0.5: 2.0, 1.0: 3.0}, "must be in"),
        ({-0.1: 1.0, 0.5: 2.0}, "must be in"),
        ({0.5: math.nan}, "must be finite"),
        ({0.25: 1.0, 0.5: math.inf, 0.75: 3.0}, "must be finite"),
        ({0.25: 3.0, 0.5: 2.0, 0.75: 4.0}, "must not cross"),
        ({0.25: 1.0, 0.5: 5.0, 0.75: 4.0}, "must not cross"),
        ({0.25: 1.0, 0.5: 2.0, 0.25 + 1e-12: 1.5, 0.75: 3.0}, "appears twice"),
    ],
)
def test_invalid_forecasts_are_rejected(forecast: dict[float, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        weighted_interval_score(forecast, 2.0)


@pytest.mark.parametrize("observed", [math.nan, math.inf, -math.inf])
def test_the_observed_value_must_be_finite(observed: float) -> None:
    for score in (weighted_interval_score, absolute_error, central_coverage):
        with pytest.raises(ValueError, match="observed value must be finite"):
            score(FORECAST, observed)


# --- an independent implementation to check WIS against ------------------------------------


def pinball(level: float, quantile: float, observed: float) -> float:
    return ((1.0 if observed < quantile else 0.0) - level) * (quantile - observed)


def wis_from_pinball_losses(forecast: Mapping[float, float], observed: float) -> float:
    """WIS written as the average pinball loss over all quantile levels.

    With K intervals there are 2K + 1 quantiles, and WIS = 2 / (2K + 1) * sum of pinball losses.
    """
    count = len(forecast)
    return 2.0 / count * sum(pinball(level, value, observed) for level, value in forecast.items())


@st.composite
def quantile_forecasts(draw: st.DrawFn) -> dict[float, float]:
    lows = draw(
        st.lists(
            st.sampled_from([0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.45]),
            unique=True,
            max_size=6,
        )
    )
    levels = sorted([*lows, 0.5, *(round(1.0 - level, 9) for level in lows)])
    values = sorted(
        draw(
            st.lists(
                st.floats(min_value=-1000, max_value=1000),
                min_size=len(levels),
                max_size=len(levels),
            )
        )
    )
    return dict(zip(levels, values, strict=True))


@given(forecast=quantile_forecasts(), observed=st.floats(min_value=-1000, max_value=1000))
def test_wis_equals_the_average_pinball_loss(forecast: dict[float, float], observed: float) -> None:
    assert weighted_interval_score(forecast, observed) == pytest.approx(
        wis_from_pinball_losses(forecast, observed), rel=1e-9, abs=1e-9
    )


@given(forecast=quantile_forecasts(), observed=st.floats(min_value=-1000, max_value=1000))
def test_wis_is_never_negative(forecast: dict[float, float], observed: float) -> None:
    assert weighted_interval_score(forecast, observed) >= 0.0
