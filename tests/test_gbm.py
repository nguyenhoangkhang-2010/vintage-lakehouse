"""Tests for the gradient boosting baseline.

Sanity checks (documented in docs/BASELINES.md):
- On an exactly periodic series it forecasts close to the next season's value.
- Its quantiles never cross, and the output is a valid quantile forecast.
- It is deterministic for a fixed seed.
- The training rows line up with their labels, and respect the row cap.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest

from series_fixture import history_from_values, seasonal_values, target_dates, with_noise
from vintage_lakehouse.forecast import InsufficientHistory, regularize
from vintage_lakehouse.replay import normalise_quantiles

if TYPE_CHECKING:
    from vintage_lakehouse.forecast.gbm import GradientBoostingForecaster
else:
    # Skips this whole module when the optional baselines dependencies are not installed.
    GradientBoostingForecaster = pytest.importorskip(
        "vintage_lakehouse.forecast.gbm"
    ).GradientBoostingForecaster


def fast(**overrides: object) -> GradientBoostingForecaster:
    options: dict[str, object] = {"n_estimators": 25, "max_depth": 3}
    options.update(overrides)
    return GradientBoostingForecaster(**options)  # type: ignore[arg-type]


def test_a_periodic_series_is_forecast_close_to_the_next_season() -> None:
    values = seasonal_values(260)
    history = history_from_values(values)
    horizons = [1, 2, 3]
    targets = target_dates(history, horizons)
    forecast = fast().forecast(history, targets)
    expected = seasonal_values(260 + 3)
    for horizon, target in zip(horizons, targets, strict=True):
        assert forecast[target][0.5] == pytest.approx(expected[259 + horizon], abs=0.5)


def test_quantiles_never_cross_and_the_output_is_valid() -> None:
    history = history_from_values(with_noise(seasonal_values(200), sd=0.4, seed=4))
    targets = target_dates(history, [1, 2, 3, 4])
    for quantiles in fast().forecast(history, targets).values():
        normalise_quantiles(quantiles)
        values = [quantiles[level] for level in sorted(quantiles)]
        assert values == sorted(values)


def test_the_forecast_is_deterministic_for_a_fixed_seed() -> None:
    history = history_from_values(with_noise(seasonal_values(180), sd=0.3, seed=8))
    targets = target_dates(history, [1, 3])
    assert fast().forecast(history, targets) == fast().forecast(history, targets)


def test_the_lower_bound_is_applied() -> None:
    history = history_from_values(
        with_noise(seasonal_values(180, base=0.5, amplitude=0.4), sd=0.3, seed=1)
    )
    targets = target_dates(history, [1, 2])
    for quantiles in fast(lower_bound=0.0).forecast(history, targets).values():
        assert min(quantiles.values()) >= 0.0


def test_only_the_requested_horizons_are_fitted_and_returned() -> None:
    history = history_from_values(seasonal_values(150))
    targets = target_dates(history, [2])
    assert set(fast().forecast(history, targets)) == set(targets)


def test_too_short_a_history_is_reported() -> None:
    model = fast()
    assert model.min_history == 52 + 8 + 40
    history = history_from_values(seasonal_values(80))
    with pytest.raises(InsufficientHistory, match="needs at least 100"):
        model.forecast(history, target_dates(history, [1]))


def test_a_horizon_beyond_one_season_is_rejected() -> None:
    history = history_from_values(seasonal_values(150))
    with pytest.raises(ValueError, match="beyond one season"):
        fast().forecast(history, target_dates(history, [53]))


def test_targets_must_lie_after_the_history() -> None:
    history = history_from_values(seasonal_values(150))
    with pytest.raises(ValueError, match="positive whole number"):
        fast().forecast(history, [history[-1].event_time])


def test_features_describe_the_situation_at_a_period() -> None:
    model = GradientBoostingForecaster(lags=2, season=4, min_train_rows=10, max_train_rows=20)
    series = regularize(history_from_values([float(i) for i in range(20)]))
    features = model._features(series, last=10, horizon=2)
    angle = 2.0 * math.pi * (series.date_at(12).timetuple().tm_yday - 1) / 365.25
    # last two values, the value one season (4 steps) before the target (index 12 - 4 = 8),
    # then the sine and cosine of the target's day of the year.
    assert features == pytest.approx([10.0, 9.0, 8.0, math.sin(angle), math.cos(angle)])


def test_training_rows_line_up_with_their_labels() -> None:
    model = GradientBoostingForecaster(lags=2, season=4, min_train_rows=10, max_train_rows=100)
    series = regularize(history_from_values([float(i) for i in range(20)]))
    features, labels = model._training_rows(series, horizon=2)
    # Rows start where the lags and the seasonal look-back exist, and end where a label exists.
    assert list(labels) == [float(i) for i in range(4, 20)]
    assert list(features[0][:3]) == [2.0, 1.0, 0.0]  # period 2: lags 2 and 1, one season before 4
    assert features.shape == (16, 5)


def test_the_number_of_training_rows_is_capped_to_the_most_recent() -> None:
    model = GradientBoostingForecaster(lags=2, season=4, min_train_rows=10, max_train_rows=12)
    series = regularize(history_from_values([float(i) for i in range(60)]))
    _, labels = model._training_rows(series, horizon=1)
    assert len(labels) == 12
    assert labels[-1] == 59.0  # the most recent label is kept


@pytest.mark.parametrize(
    "overrides",
    [
        {"lags": 0},
        {"season": 0},
        {"min_train_rows": 5},
        {"max_train_rows": 10, "min_train_rows": 20},
    ],
)
def test_invalid_settings_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        GradientBoostingForecaster(**overrides)  # type: ignore[arg-type]


def test_invalid_levels_are_rejected() -> None:
    with pytest.raises(ValueError, match="symmetric"):
        GradientBoostingForecaster(levels=[0.5, 0.9])
