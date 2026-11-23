"""Seasonal naive: the value one season ago, with an error spread learned from the history.

The median for a target is the value of the same period one season earlier (52 weeks for weekly
data). The spread comes from the errors this rule would have made in the past: for every earlier
period, the difference between its value and the value one season before it. Errors from periods
at a similar point of the season (within ``window`` steps) are preferred, because the error is
larger in some parts of the season than in others.

It ignores the most recent values, so revisions to them hardly affect it. That makes it a useful
contrast to models that extrapolate from the latest value.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from vintage_lakehouse.forecast.series import (
    DEFAULT_LEVELS,
    InsufficientHistory,
    check_levels,
    empirical_quantile,
    finish,
    regularize,
    steps_ahead,
)
from vintage_lakehouse.replay.harness import Forecast
from vintage_lakehouse.storage.model import Observation


class SeasonalNaive:
    """Forecast = value one season earlier + quantiles of past seasonal errors."""

    def __init__(
        self,
        *,
        season: int = 52,
        window: int = 4,
        min_errors: int = 8,
        levels: Sequence[float] = DEFAULT_LEVELS,
        lower_bound: float | None = None,
        step_days: int = 7,
    ) -> None:
        if season < 1 or window < 0 or min_errors < 2:
            raise ValueError("season must be >= 1, window >= 0 and min_errors >= 2")
        self.name = "seasonal_naive"
        self.season = season
        self.window = window
        self.min_errors = min_errors
        self.levels = check_levels(levels)
        self.lower_bound = lower_bound
        self.step_days = step_days
        #: Periods needed to have at least ``min_errors`` past seasonal errors.
        self.min_history = season + min_errors

    def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
        """Forecast each target; see ``Forecaster`` in ``vintage_lakehouse.replay``."""
        series = regularize(history, self.step_days)
        if series.n < self.min_history:
            raise InsufficientHistory(
                f"{self.name} needs at least {self.min_history} periods, got {series.n}"
            )
        values = series.values
        errors = [
            (index, values[index] - values[index - self.season])
            for index in range(self.season, series.n)
        ]

        out: dict[date, dict[float, float]] = {}
        for target in targets:
            horizon = steps_ahead(series, target)
            index = series.n - 1 + horizon
            # The latest period one or more whole seasons before the target that is already known.
            seasons_back = -(-horizon // self.season)
            analogue = values[index - seasons_back * self.season]

            nearby = sorted(
                error
                for position, error in errors
                if self._season_distance(position, index) <= self.window
            )
            spread = nearby if len(nearby) >= self.min_errors else sorted(e for _, e in errors)
            out[target] = finish(
                {level: analogue + empirical_quantile(spread, level) for level in self.levels},
                self.lower_bound,
            )
        return out

    def _season_distance(self, a: int, b: int) -> int:
        """Circular distance between two positions within the season."""
        gap = abs(a - b) % self.season
        return min(gap, self.season - gap)
