"""Gradient boosting on lagged values, with one quantile-regression model per level.

For each requested horizon ``h`` the model is trained on the history itself: the features
describe the situation at an earlier period (its last ``lags`` values, the value one season
before the target, and the day of the year of the target as a sine and cosine) and the label is
the value ``h`` periods later. Each quantile level has its own model, trained with the pinball
loss, and the predicted quantiles are put in order so they never cross. Only the most recent
``max_train_rows`` rows are used, which caps the cost as the history grows.

It is deterministic: every model uses a fixed random seed.

Requires the optional ``baselines`` dependencies: ``pip install "vintage-lakehouse[baselines]"``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import GradientBoostingRegressor

from vintage_lakehouse.forecast.series import (
    DEFAULT_LEVELS,
    InsufficientHistory,
    RegularSeries,
    check_levels,
    finish,
    regularize,
    steps_ahead,
)
from vintage_lakehouse.replay.harness import Forecast
from vintage_lakehouse.storage.model import Observation


class GradientBoostingForecaster:
    """Direct multi-horizon quantile gradient boosting on lagged values."""

    def __init__(
        self,
        *,
        lags: int = 8,
        season: int = 52,
        n_estimators: int = 50,
        max_depth: int = 3,
        learning_rate: float = 0.1,
        min_samples_leaf: int = 5,
        max_features: float = 0.5,
        max_train_rows: int = 416,
        min_train_rows: int = 40,
        levels: Sequence[float] = DEFAULT_LEVELS,
        lower_bound: float | None = None,
        step_days: int = 7,
        seed: int = 0,
    ) -> None:
        if lags < 1 or season < 1 or min_train_rows < 10 or max_train_rows < min_train_rows:
            raise ValueError("invalid lags, season or training-row limits")
        self.name = "gbm"
        self.lags = lags
        self.season = season
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.min_samples_leaf = min_samples_leaf
        self.max_features = max_features
        self.max_train_rows = max_train_rows
        self.min_train_rows = min_train_rows
        self.levels = check_levels(levels)
        self.lower_bound = lower_bound
        self.step_days = step_days
        self.seed = seed
        #: Enough periods for the lags, one season of look-back, and the minimum training rows.
        self.min_history = season + lags + min_train_rows

    def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
        """Forecast each target; see ``Forecaster`` in ``vintage_lakehouse.replay``."""
        series = regularize(history, self.step_days)
        if series.n < self.min_history:
            raise InsufficientHistory(
                f"{self.name} needs at least {self.min_history} periods, got {series.n}"
            )
        by_horizon: dict[int, list[date]] = {}
        for target in targets:
            horizon = steps_ahead(series, target)
            if horizon > self.season:
                raise ValueError(
                    f"horizon {horizon} is beyond one season ({self.season} steps) for {self.name}"
                )
            by_horizon.setdefault(horizon, []).append(target)

        out: dict[date, dict[float, float]] = {}
        for horizon, horizon_targets in by_horizon.items():
            features, labels = self._training_rows(series, horizon)
            if len(labels) < self.min_train_rows:
                raise InsufficientHistory(
                    f"{self.name} has only {len(labels)} training rows for horizon {horizon}, "
                    f"needs {self.min_train_rows}"
                )
            models = {level: self._fit(level, features, labels) for level in self.levels}
            row = np.asarray([self._features(series, series.n - 1, horizon)])
            predicted = {level: float(model.predict(row)[0]) for level, model in models.items()}
            for target in horizon_targets:
                if any(not math.isfinite(v) for v in predicted.values()):
                    raise ValueError(f"{self.name} produced a non-finite forecast")
                out[target] = finish(predicted, self.lower_bound)
        return out

    def _features(self, series: RegularSeries, last: int, horizon: int) -> list[float]:
        """Features describing the situation at period ``last`` for a target ``horizon`` later."""
        values = series.values
        target_index = last + horizon
        angle = 2.0 * math.pi * (series.date_at(target_index).timetuple().tm_yday - 1) / 365.25
        recent = [values[last - lag] for lag in range(self.lags)]
        return [*recent, values[target_index - self.season], math.sin(angle), math.cos(angle)]

    def _training_rows(
        self, series: RegularSeries, horizon: int
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        first = max(self.lags - 1, self.season - horizon)
        last_start = series.n - 1 - horizon
        start = max(first, last_start - self.max_train_rows + 1)
        rows = [self._features(series, i, horizon) for i in range(start, last_start + 1)]
        labels = [series.values[i + horizon] for i in range(start, last_start + 1)]
        return np.asarray(rows, dtype=float), np.asarray(labels, dtype=float)

    def _fit(
        self, level: float, features: NDArray[np.float64], labels: NDArray[np.float64]
    ) -> GradientBoostingRegressor:
        return GradientBoostingRegressor(
            loss="quantile",
            alpha=level,
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            min_samples_leaf=self.min_samples_leaf,
            max_features=self.max_features,
            random_state=self.seed,
        ).fit(features, labels)
