"""Configuration and orchestration of the leakage-gap experiment.

The experiment replays the baselines over a grid of forecast origins at several evaluation
times ``T_eval``, then analyses the scores in several scenarios (each ``T_eval``, with and
without a set of excluded origins). Real-time forecasts do not depend on ``T_eval``, so they are
cached and computed once; only the hindsight forecasts and the truth change.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from vintage_lakehouse.experiment.analysis import (
    Analysis,
    RevisionCorrelation,
    RevisionGroup,
    analyze_rows,
    revision_analysis,
)
from vintage_lakehouse.experiment.bootstrap import make_replicates
from vintage_lakehouse.forecast import SeasonalNaive
from vintage_lakehouse.replay import (
    Forecast,
    Forecaster,
    ReplayConfig,
    ScoredForecast,
    SkippedOrigin,
    run_replay,
)
from vintage_lakehouse.storage.base import AsOfStore
from vintage_lakehouse.storage.model import Observation, to_utc

UTC = timezone.utc
MODEL_NAMES = ("seasonal_naive", "ets", "gbm")
_Exclusion = tuple[datetime, datetime]


@dataclass(frozen=True)
class Scenario:
    """One way of slicing the results: an evaluation time and a set of excluded origins."""

    evaluation_time: datetime
    excluded: tuple[_Exclusion, ...] = ()

    @property
    def label(self) -> str:
        """A short description, used in reports."""
        base = f"T_eval={self.evaluation_time:%Y-%m-%d}"
        if not self.excluded:
            return f"{base}, all origins"
        spans = "; ".join(f"{a:%Y-%m-%d} to {b:%Y-%m-%d}" for a, b in self.excluded)
        return f"{base}, excluding origins {spans}"

    def keeps(self, origin: datetime) -> bool:
        """Whether an origin is part of this scenario."""
        return not any(start <= origin <= end for start, end in self.excluded)


@dataclass(frozen=True)
class ExperimentConfig:
    """Everything that defines a run. Two runs with the same config and data agree."""

    series_id: str = "delphi/fluview/nat/wili"
    models: tuple[str, ...] = MODEL_NAMES
    first_origin: datetime = datetime(2015, 1, 3, tzinfo=UTC)
    last_origin: datetime = datetime(2025, 6, 28, tzinfo=UTC)
    origin_step_weeks: int = 4
    horizons: tuple[int, ...] = (1, 2, 3, 4)
    evaluation_time: datetime = datetime(2026, 9, 23, tzinfo=UTC)
    sensitivity_weeks: tuple[int, ...] = (13, 26, 52)
    exclusions: tuple[_Exclusion, ...] = (
        (datetime(2020, 3, 1, tzinfo=UTC), datetime(2021, 9, 30, tzinfo=UTC)),
    )
    n_boot: int = 2000
    block_weeks: int = 12
    confidence: float = 0.95
    seed: int = 0
    revision_window: int = 4
    step_days: int = 7
    lower_bound: float | None = 0.0

    def __post_init__(self) -> None:
        unknown = [name for name in self.models if name not in MODEL_NAMES]
        if not self.models or unknown or len(set(self.models)) != len(self.models):
            raise ValueError(f"models must be unique names from {MODEL_NAMES}, got {self.models}")
        horizons = tuple(sorted(set(self.horizons)))
        if not horizons or horizons[0] < 1:
            raise ValueError("horizons must be a non-empty set of positive integers")
        for name, minimum in (
            ("origin_step_weeks", 1),
            ("block_weeks", 1),
            ("n_boot", 1),
            ("revision_window", 1),
            ("step_days", 1),
        ):
            if getattr(self, name) < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
        if not 0.0 < self.confidence < 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if any(weeks < 1 for weeks in self.sensitivity_weeks):
            raise ValueError("sensitivity_weeks must be positive")

        first = to_utc(self.first_origin, "first_origin")
        last = to_utc(self.last_origin, "last_origin")
        evaluation = to_utc(self.evaluation_time, "evaluation_time")
        if first > last:
            raise ValueError("first_origin must not be after last_origin")
        if evaluation < last:
            raise ValueError("evaluation_time must not be before last_origin")
        exclusions = tuple(
            (to_utc(a, "exclusion"), to_utc(b, "exclusion")) for a, b in self.exclusions
        )
        if any(a > b for a, b in exclusions):
            raise ValueError("each exclusion must start before it ends")
        object.__setattr__(self, "horizons", horizons)
        object.__setattr__(self, "first_origin", first)
        object.__setattr__(self, "last_origin", last)
        object.__setattr__(self, "evaluation_time", evaluation)
        object.__setattr__(self, "exclusions", exclusions)

    def origins(self) -> list[datetime]:
        """The forecast origins: evenly spaced from the first to (at most) the last."""
        step = timedelta(weeks=self.origin_step_weeks)
        origins = []
        current = self.first_origin
        while current <= self.last_origin:
            origins.append(current)
            current += step
        return origins

    def evaluation_times(self) -> list[datetime]:
        """Primary ``T_eval`` and the earlier ones used for the sensitivity analysis."""
        earlier = {
            self.last_origin + timedelta(weeks=weeks)
            for weeks in self.sensitivity_weeks
            if self.last_origin + timedelta(weeks=weeks) < self.evaluation_time
        }
        return sorted({*earlier, self.evaluation_time})

    def block_length(self) -> int:
        """Bootstrap block length, in origins (the block length in weeks rounded up)."""
        return max(1, math.ceil(self.block_weeks / self.origin_step_weeks))

    @property
    def primary(self) -> Scenario:
        """The scenario of the headline result: primary ``T_eval``, all origins."""
        return Scenario(self.evaluation_time)

    def scenarios(self) -> list[Scenario]:
        """Every scenario analysed: each ``T_eval``, all origins, then with the exclusions."""
        scenarios: list[Scenario] = []
        for evaluation_time in self.evaluation_times():
            scenarios.append(Scenario(evaluation_time))
            if self.exclusions:
                scenarios.append(Scenario(evaluation_time, self.exclusions))
        return scenarios


def make_forecasters(names: Sequence[str], lower_bound: float | None) -> list[Forecaster]:
    """Build the named baselines. ETS and gradient boosting need the ``baselines`` extra."""
    forecasters: list[Forecaster] = []
    for name in names:
        if name == "seasonal_naive":
            forecasters.append(SeasonalNaive(lower_bound=lower_bound))
        elif name == "ets":
            from vintage_lakehouse.forecast.ets import ETSForecaster

            forecasters.append(ETSForecaster(lower_bound=lower_bound))
        elif name == "gbm":
            from vintage_lakehouse.forecast.gbm import GradientBoostingForecaster

            forecasters.append(GradientBoostingForecaster(lower_bound=lower_bound))
        else:
            raise ValueError(f"unknown model {name!r}; choose from {MODEL_NAMES}")
    return forecasters


class CachingForecaster:
    """Remembers forecasts, so that identical histories are never forecast twice.

    Real-time histories are the same whatever the evaluation time, so across evaluation times
    only the hindsight forecasts are recomputed. This relies on forecasters being deterministic,
    which the replay harness already requires.
    """

    def __init__(self, inner: Forecaster) -> None:
        self.name = inner.name
        self._inner = inner
        self._cache: dict[tuple[tuple[Observation, ...], date], dict[float, float]] = {}
        self.requested = 0
        self.computed = 0

    def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
        """Forecast each target, computing only those not seen before for this history."""
        key = tuple(history)
        missing = [target for target in targets if (key, target) not in self._cache]
        self.requested += len(targets)
        self.computed += len(missing)
        if missing:
            produced = self._inner.forecast(history, missing)
            for target in missing:
                if target not in produced:
                    raise ValueError(f"{self.name} returned no forecast for {target}")
                self._cache[(key, target)] = dict(produced[target])
        return {target: dict(self._cache[(key, target)]) for target in targets}


@dataclass(frozen=True)
class ExperimentResult:
    """The scores of a run, by evaluation time, with what is needed to interpret them."""

    config: ExperimentConfig
    rows_by_eval: dict[datetime, list[ScoredForecast]]
    skipped_by_eval: dict[datetime, list[SkippedOrigin]]
    revision_sizes: dict[datetime, float]
    seconds_by_eval: dict[datetime, float]
    cache_requested: dict[str, int]
    cache_computed: dict[str, int]


def recent_revision_sizes(
    store: AsOfStore,
    series_id: str,
    origins: Sequence[datetime],
    evaluation_time: datetime,
    window: int,
) -> dict[datetime, float]:
    """For each origin, how much its latest reported periods were later revised, on average.

    Takes the last ``window`` periods known at the origin and returns the mean absolute
    difference between their value then and their value at ``evaluation_time``.
    """
    final = {o.event_time: o.value for o in store.as_of(series_id, evaluation_time)}
    sizes: dict[datetime, float] = {}
    for origin in origins:
        recent = store.as_of(series_id, origin)[-window:]
        differences = [abs(final[o.event_time] - o.value) for o in recent if o.event_time in final]
        if differences:
            sizes[origin] = math.fsum(differences) / len(differences)
    return sizes


def run_experiment(
    store: AsOfStore,
    forecasters: Sequence[Forecaster],
    config: ExperimentConfig,
    *,
    progress: Callable[[str], None] | None = None,
) -> ExperimentResult:
    """Replay every origin at every evaluation time and collect the scores."""
    say = progress or (lambda message: None)
    cached = [CachingForecaster(forecaster) for forecaster in forecasters]
    min_history = max((getattr(f, "min_history", 1) for f in forecasters), default=1)
    origins = config.origins()

    rows: dict[datetime, list[ScoredForecast]] = {}
    skipped: dict[datetime, list[SkippedOrigin]] = {}
    seconds: dict[datetime, float] = {}
    # Latest first: its real-time forecasts cover every later run, which then only add hindsight.
    for evaluation_time in sorted(config.evaluation_times(), reverse=True):
        say(
            f"replaying with T_eval={evaluation_time:%Y-%m-%d} "
            f"({len(origins)} origins x {len(cached)} models) ..."
        )
        started = time.perf_counter()
        result = run_replay(
            store,
            cached,
            ReplayConfig(
                series_id=config.series_id,
                origins=origins,
                horizons=config.horizons,
                evaluation_time=evaluation_time,
                step=timedelta(days=config.step_days),
                min_history=min_history,
            ),
        )
        elapsed = time.perf_counter() - started
        rows[evaluation_time], skipped[evaluation_time], seconds[evaluation_time] = (
            result.rows,
            result.skipped,
            elapsed,
        )
        say(
            f"  done in {elapsed:.0f}s: {len(result.rows)} scored forecasts, "
            f"{len(result.skipped)} skipped origins"
        )

    sizes = recent_revision_sizes(
        store, config.series_id, origins, config.evaluation_time, config.revision_window
    )
    ascending = sorted(rows)
    return ExperimentResult(
        config=config,
        rows_by_eval={t: rows[t] for t in ascending},
        skipped_by_eval={t: skipped[t] for t in ascending},
        revision_sizes=sizes,
        seconds_by_eval={t: seconds[t] for t in ascending},
        cache_requested={c.name: c.requested for c in cached},
        cache_computed={c.name: c.computed for c in cached},
    )


@dataclass(frozen=True)
class ExperimentAnalyses:
    """All statistics of a run."""

    by_scenario: dict[Scenario, Analysis]
    revision_groups: list[RevisionGroup]
    revision_correlations: list[RevisionCorrelation]


def run_analyses(result: ExperimentResult) -> ExperimentAnalyses:
    """Bootstrap the leakage gap in every scenario, and relate it to revision size."""
    config = result.config
    by_scenario: dict[Scenario, Analysis] = {}
    for scenario in config.scenarios():
        rows = [
            r for r in result.rows_by_eval[scenario.evaluation_time] if scenario.keeps(r.origin)
        ]
        by_scenario[scenario] = analyze_rows(
            rows,
            n_boot=config.n_boot,
            block_length=config.block_length(),
            level=config.confidence,
            seed=config.seed,
        )

    primary_rows = result.rows_by_eval[config.evaluation_time]
    n_origins = len({row.origin for row in primary_rows})
    groups: list[RevisionGroup] = []
    correlations: list[RevisionCorrelation] = []
    if n_origins:
        replicates = make_replicates(
            n_origins, n_boot=config.n_boot, block_length=config.block_length(), seed=config.seed
        )
        groups, correlations = revision_analysis(
            primary_rows,
            result.revision_sizes,
            replicates=replicates,
            level=config.confidence,
        )
    return ExperimentAnalyses(by_scenario, groups, correlations)
