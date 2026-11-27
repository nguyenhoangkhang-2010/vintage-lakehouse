"""Tests for the seasonal naive baseline.

Sanity checks (documented in docs/BASELINES.md):
- On an exactly periodic series it forecasts the next season exactly, with no spread.
- With a constant seasonal error it shifts the forecast by that error.
- Its quantiles match values worked out by hand from the past seasonal errors.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from series_fixture import history_from_values, seasonal_values, target_dates
from vintage_lakehouse.forecast import InsufficientHistory, SeasonalNaive
from vintage_lakehouse.replay import normalise_quantiles

# Hand-worked example, season of 4 steps. The seasonal errors v[i] - v[i-4] for i = 4..15 are
# [1, 0, 0, 0, -1, 0, 0, 0, 2, 0, 0, 0].
SPIKY = [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 2, 0, 0, 0]


def small(**overrides: object) -> SeasonalNaive:
    options: dict[str, object] = {"season": 4, "window": 4, "min_errors": 2}
    options.update(overrides)
    return SeasonalNaive(**options)  # type: ignore[arg-type]


def test_a_periodic_series_is_forecast_exactly_with_no_spread() -> None:
    history = history_from_values(seasonal_values(130))
    targets = target_dates(history, [1, 2, 10, 52])
    forecast = SeasonalNaive().forecast(history, targets)
    for horizon, target in zip([1, 2, 10, 52], targets, strict=True):
        index = 129 + horizon
        expected = seasonal_values(index + 1)[index]
        assert set(forecast[target].values()) == {expected}


def test_a_constant_seasonal_error_shifts_the_forecast() -> None:
    history = history_from_values([1, 2, 3, 4, 2, 3, 4, 5, 3, 4, 5, 6])
    one, two = target_dates(history, [1, 2])
    forecast = small().forecast(history, [one, two])
    # Every past seasonal error is +1, and a season ago the values were 3 and 4.
    assert set(forecast[one].values()) == {4.0}
    assert set(forecast[two].values()) == {5.0}


def test_quantiles_follow_the_seasonal_errors_worked_out_by_hand() -> None:
    history = history_from_values(SPIKY)
    [target] = target_dates(history, [1])
    quantiles = small().forecast(history, [target])[target]
    # The analogue of the target is the 2 at index 12; error quantiles come from
    # sorted errors [-1, 0 x9, 1, 2] with linear interpolation.
    assert quantiles[0.025] == pytest.approx(2 - 0.725)
    assert quantiles[0.1] == pytest.approx(2.0)
    assert quantiles[0.25] == pytest.approx(2.0)
    assert quantiles[0.5] == pytest.approx(2.0)
    assert quantiles[0.75] == pytest.approx(2.0)
    assert quantiles[0.9] == pytest.approx(2.9)
    assert quantiles[0.975] == pytest.approx(2 + 1.725)


def test_only_errors_from_a_similar_point_of_the_season_are_used_when_enough_exist() -> None:
    history = history_from_values(SPIKY)
    [target] = target_dates(history, [1])
    # window=0: the target sits at season position 0, whose past errors are 1, -1 and 2.
    quantiles = small(window=0).forecast(history, [target])[target]
    assert quantiles[0.5] == pytest.approx(2 + 1.0)
    assert quantiles[0.975] == pytest.approx(2 + 1.95)
    assert quantiles[0.025] == pytest.approx(2 - 0.9)


def test_all_errors_are_used_when_too_few_are_nearby() -> None:
    history = history_from_values(SPIKY)
    [target] = target_dates(history, [1])
    narrow_but_demanding = small(window=0, min_errors=4).forecast(history, [target])[target]
    everything = small().forecast(history, [target])[target]
    assert narrow_but_demanding == everything


def test_a_horizon_longer_than_a_season_uses_the_latest_known_earlier_season() -> None:
    history = history_from_values(SPIKY)
    [target] = target_dates(history, [5])
    # The target is index 20; two seasons back is index 12, whose value is 2.
    assert small().forecast(history, [target])[target][0.5] == pytest.approx(2.0)


def test_the_lower_bound_is_applied() -> None:
    history = history_from_values(SPIKY)
    [target] = target_dates(history, [1])
    bounded = small(lower_bound=1.5).forecast(history, [target])[target]
    assert min(bounded.values()) == 1.5
    assert bounded[0.9] == pytest.approx(2.9)


def test_the_output_is_a_valid_quantile_forecast() -> None:
    history = history_from_values(seasonal_values(130, amplitude=2.0))
    targets = target_dates(history, [1, 2, 3, 4])
    for quantiles in SeasonalNaive().forecast(history, targets).values():
        normalise_quantiles(quantiles)  # raises if invalid


def test_too_short_a_history_is_reported() -> None:
    model = small()
    assert model.min_history == 6
    history = history_from_values([1.0, 2.0, 3.0, 4.0, 5.0])
    with pytest.raises(InsufficientHistory, match="needs at least 6"):
        model.forecast(history, target_dates(history, [1]))


def test_targets_must_lie_after_the_history() -> None:
    history = history_from_values(SPIKY)
    with pytest.raises(ValueError, match="positive whole number"):
        small().forecast(history, [history[-1].event_time])
    with pytest.raises(ValueError, match="positive whole number"):
        small().forecast(history, [history[-1].event_time + timedelta(days=3)])


def test_no_targets_means_no_forecasts() -> None:
    assert small().forecast(history_from_values(SPIKY), []) == {}


def test_the_forecast_is_deterministic_and_leaves_the_history_alone() -> None:
    history = history_from_values(SPIKY)
    snapshot = list(history)
    targets = target_dates(history, [1, 2])
    assert small().forecast(history, targets) == small().forecast(history, targets)
    assert history == snapshot


@pytest.mark.parametrize(
    "overrides",
    [{"season": 0}, {"window": -1}, {"min_errors": 1}, {"levels": [0.5, 0.75]}],
)
def test_invalid_settings_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        small(**overrides)
