"""Baseline forecasters used by the replay harness.

Available here without extra dependencies: ``SeasonalNaive`` and the shared helpers. The ETS and
gradient boosting baselines live in ``vintage_lakehouse.forecast.ets`` and
``vintage_lakehouse.forecast.gbm`` and need the optional ``baselines`` extra
(``pip install "vintage-lakehouse[baselines]"``). See docs/BASELINES.md.
"""

from vintage_lakehouse.forecast.seasonal_naive import SeasonalNaive
from vintage_lakehouse.forecast.series import (
    DEFAULT_LEVELS,
    InsufficientHistory,
    RegularSeries,
    empirical_quantile,
    regularize,
    steps_ahead,
)

__all__ = [
    "DEFAULT_LEVELS",
    "InsufficientHistory",
    "RegularSeries",
    "SeasonalNaive",
    "empirical_quantile",
    "regularize",
    "steps_ahead",
]
