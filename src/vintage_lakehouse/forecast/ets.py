"""Exponential smoothing (ETS) with a damped additive trend.

A state-space exponential smoothing model with additive errors and a damped additive trend,
fitted to the whole history by maximum likelihood (statsmodels ``ETSModel``). The prediction
interval of the fitted model gives the spread at each horizon, treated as normal. There is no
seasonal component, so this baseline captures level and recent momentum, and widens its interval
with the horizon.

It extrapolates from the latest values, so it reacts strongly to revisions of them.

Requires the optional ``baselines`` dependencies: ``pip install "vintage-lakehouse[baselines]"``.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from datetime import date
from statistics import NormalDist

import pandas as pd
from statsmodels.tsa.exponential_smoothing.ets import ETSModel

from vintage_lakehouse.forecast.series import (
    DEFAULT_LEVELS,
    InsufficientHistory,
    check_levels,
    finish,
    normal_quantiles,
    regularize,
    steps_ahead,
)
from vintage_lakehouse.replay.harness import Forecast
from vintage_lakehouse.storage.model import Observation

_Z_95 = NormalDist().inv_cdf(0.975)


class ForecastFailure(RuntimeError):
    """The model could not be fitted or produced an unusable forecast."""


class ETSForecaster:
    """Additive-error ETS with a damped additive trend and normal prediction intervals."""

    def __init__(
        self,
        *,
        damped_trend: bool = True,
        min_history: int = 52,
        levels: Sequence[float] = DEFAULT_LEVELS,
        lower_bound: float | None = None,
        step_days: int = 7,
    ) -> None:
        self.name = "ets"
        self.damped_trend = damped_trend
        self.min_history = min_history
        self.levels = check_levels(levels)
        self.lower_bound = lower_bound
        self.step_days = step_days

    def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
        """Forecast each target; see ``Forecaster`` in ``vintage_lakehouse.replay``."""
        series = regularize(history, self.step_days)
        if series.n < self.min_history:
            raise InsufficientHistory(
                f"{self.name} needs at least {self.min_history} periods, got {series.n}"
            )
        horizons = {target: steps_ahead(series, target) for target in targets}
        longest = max(horizons.values(), default=0)
        if longest == 0:
            return {}

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fitted = ETSModel(
                    pd.Series(series.values),
                    error="add",
                    trend="add",
                    damped_trend=self.damped_trend,
                    seasonal=None,
                ).fit(disp=False)
                frame = fitted.get_prediction(
                    start=series.n, end=series.n + longest - 1
                ).summary_frame(alpha=0.05)
        except Exception as exc:  # statsmodels raises many kinds of numerical errors
            raise ForecastFailure(f"{self.name} could not be fitted: {exc}") from exc

        out: dict[date, dict[float, float]] = {}
        for target, horizon in horizons.items():
            row = frame.iloc[horizon - 1]
            mean = float(row["mean"])
            sd = (float(row["pi_upper"]) - float(row["pi_lower"])) / (2.0 * _Z_95)
            try:
                quantiles = normal_quantiles(mean, sd, self.levels)
            except ValueError as exc:
                raise ForecastFailure(f"{self.name} gave an unusable forecast: {exc}") from exc
            out[target] = finish(quantiles, self.lower_bound)
        return out
