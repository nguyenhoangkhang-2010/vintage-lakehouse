"""Tests for the ETS baseline.

Sanity checks (documented in docs/BASELINES.md):
- A constant series is forecast as that constant, with almost no spread.
- A steadily rising series is forecast to keep rising, close to the next value.
- The prediction interval never narrows as the horizon grows.
- The forecast is deterministic and a valid quantile forecast.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from series_fixture import history_from_values, target_dates, with_noise
from vintage_lakehouse.forecast import InsufficientHistory
from vintage_lakehouse.replay import normalise_quantiles

if TYPE_CHECKING:
    from vintage_lakehouse.forecast.ets import ETSForecaster, ForecastFailure
else:
    # Skips this whole module when the optional baselines dependencies are not installed.
    _ets = pytest.importorskip("vintage_lakehouse.forecast.ets")
    ETSForecaster, ForecastFailure = _ets.ETSForecaster, _ets.ForecastFailure


def test_a_constant_series_is_forecast_as_that_constant() -> None:
    history = history_from_values([5.0] * 80)
    targets = target_dates(history, [1, 2, 4])
    for quantiles in ETSForecaster().forecast(history, targets).values():
        assert all(value == pytest.approx(5.0, abs=1e-3) for value in quantiles.values())


def test_a_rising_series_is_forecast_to_keep_rising() -> None:
    values = [0.1 * i for i in range(80)]
    history = history_from_values(values)
    [one] = target_dates(history, [1])
    median = ETSForecaster().forecast(history, [one])[one][0.5]
    assert median > values[-1]
    assert median == pytest.approx(0.1 * 80, abs=0.1)


def test_the_quantiles_match_the_prediction_intervals_of_the_fitted_model() -> None:
    # An independent check on the spread: fit the same statsmodels model directly and compare
    # its own 95% and 50% prediction intervals with the forecaster's quantiles.
    import pandas as pd
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    values = with_noise([5.0] * 120, sd=0.5, seed=11)
    history = history_from_values(values)
    targets = target_dates(history, [1, 3])
    forecast = ETSForecaster().forecast(history, targets)

    fitted = ETSModel(
        pd.Series(values), error="add", trend="add", damped_trend=True, seasonal=None
    ).fit(disp=False)
    prediction = fitted.get_prediction(start=len(values), end=len(values) + 2)
    wide = prediction.summary_frame(alpha=0.05)
    narrow = prediction.summary_frame(alpha=0.5)
    for target, row in zip(targets, [0, 2], strict=True):
        quantiles = forecast[target]
        assert quantiles[0.5] == pytest.approx(wide["mean"].iloc[row], rel=1e-6)
        assert quantiles[0.025] == pytest.approx(wide["pi_lower"].iloc[row], rel=1e-6)
        assert quantiles[0.975] == pytest.approx(wide["pi_upper"].iloc[row], rel=1e-6)
        assert quantiles[0.25] == pytest.approx(narrow["pi_lower"].iloc[row], rel=1e-6)
        assert quantiles[0.75] == pytest.approx(narrow["pi_upper"].iloc[row], rel=1e-6)


def test_the_interval_never_narrows_as_the_horizon_grows() -> None:
    history = history_from_values(with_noise([5.0] * 120, sd=0.5, seed=3))
    targets = target_dates(history, [1, 2, 3, 4])
    forecast = ETSForecaster().forecast(history, targets)
    widths = [forecast[t][0.975] - forecast[t][0.025] for t in targets]
    assert widths == sorted(widths)
    assert widths[-1] > widths[0] > 0.0


def test_the_median_sits_between_the_quartiles_and_the_output_is_valid() -> None:
    history = history_from_values(with_noise([5.0] * 100, sd=0.4, seed=5))
    targets = target_dates(history, [1, 4])
    for quantiles in ETSForecaster().forecast(history, targets).values():
        normalise_quantiles(quantiles)
        assert quantiles[0.25] <= quantiles[0.5] <= quantiles[0.75]


def test_the_lower_bound_is_applied() -> None:
    history = history_from_values(with_noise([0.2] * 100, sd=0.3, seed=2))
    targets = target_dates(history, [1, 4])
    for quantiles in ETSForecaster(lower_bound=0.0).forecast(history, targets).values():
        assert min(quantiles.values()) >= 0.0


def test_the_forecast_is_deterministic_and_leaves_the_history_alone() -> None:
    history = history_from_values(with_noise([5.0] * 100, sd=0.4, seed=9))
    snapshot = list(history)
    targets = target_dates(history, [1, 2])
    assert ETSForecaster().forecast(history, targets) == ETSForecaster().forecast(history, targets)
    assert history == snapshot


def test_too_short_a_history_is_reported() -> None:
    history = history_from_values([5.0] * 30)
    with pytest.raises(InsufficientHistory, match="needs at least 52"):
        ETSForecaster().forecast(history, target_dates(history, [1]))


def test_no_targets_means_no_forecasts() -> None:
    assert ETSForecaster().forecast(history_from_values([5.0] * 80), []) == {}


def test_targets_must_lie_after_the_history() -> None:
    history = history_from_values([5.0] * 80)
    with pytest.raises(ValueError, match="positive whole number"):
        ETSForecaster().forecast(history, [history[-1].event_time])


def test_a_model_that_cannot_be_fitted_raises_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def fit(self, **kwargs: object) -> None:
            raise RuntimeError("optimiser failed")

    monkeypatch.setattr("vintage_lakehouse.forecast.ets.ETSModel", Broken)
    history = history_from_values([5.0] * 80)
    with pytest.raises(ForecastFailure, match="could not be fitted"):
        ETSForecaster().forecast(history, target_dates(history, [1]))


def test_invalid_levels_are_rejected() -> None:
    with pytest.raises(ValueError, match="symmetric"):
        ETSForecaster(levels=[0.5, 0.9])
