"""Real-time replay, hindsight runs and forecast scoring.

``run_replay`` forecasts every origin twice, from what was known then and from what is known
now, and scores both against the same truth. ``leakage_gap`` turns the result into the headline
number. Baseline forecasters arrive in ``feature/baseline-forecasters`` and the full experiment
(confidence intervals, sensitivity to the evaluation time) in ``feature/leakage-gap-experiment``.
"""

from vintage_lakehouse.replay.harness import (
    Forecast,
    Forecaster,
    Mode,
    ReplayConfig,
    ReplayResult,
    ScoredForecast,
    SkippedOrigin,
    run_replay,
)
from vintage_lakehouse.replay.scoring import (
    absolute_error,
    central_coverage,
    normalise_quantiles,
    weighted_interval_score,
)
from vintage_lakehouse.replay.summary import LeakageGap, ModelSummary, leakage_gap, summarize

__all__ = [
    "Forecast",
    "Forecaster",
    "LeakageGap",
    "Mode",
    "ModelSummary",
    "ReplayConfig",
    "ReplayResult",
    "ScoredForecast",
    "SkippedOrigin",
    "absolute_error",
    "central_coverage",
    "leakage_gap",
    "normalise_quantiles",
    "run_replay",
    "summarize",
    "weighted_interval_score",
]
