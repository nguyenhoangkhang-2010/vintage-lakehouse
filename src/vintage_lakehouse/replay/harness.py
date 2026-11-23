"""Replay harness: run forecasters on what was known then, and on what is known now.

For every forecast origin ``t`` and every forecaster the harness makes two forecasts:

- **real-time**: the forecaster sees ``as_of(t)``, exactly what a forecaster could have had
  at ``t``;
- **hindsight**: the forecaster sees the same periods (up to the latest one reported at ``t``)
  with the values known at the evaluation time ``T_eval``, after revisions have settled.

Both are scored against the same truth, the value of each target period at ``T_eval``, so any
difference in score comes from the information available when the model was fitted. The
protocol is specified in docs/RESEARCH_QUESTION.md and docs/adr/0007-replay-protocol.md.

Forecasters must be deterministic: the same history and targets must give the same forecast.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Protocol

from vintage_lakehouse.replay.scoring import (
    absolute_error,
    central_coverage,
    normalise_quantiles,
    weighted_interval_score,
)
from vintage_lakehouse.storage.base import AsOfStore
from vintage_lakehouse.storage.model import Observation, to_utc

Forecast = Mapping[date, Mapping[float, float]]


class Mode(str, Enum):
    """Which information a forecast was made from."""

    REALTIME = "realtime"
    HINDSIGHT = "hindsight"


class Forecaster(Protocol):
    """Something that turns a history into quantile forecasts for target periods."""

    name: str

    def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
        """Forecast each of ``targets``.

        ``history`` has one observation per period, ordered by event_time. The result maps
        every target to a quantile forecast (see ``vintage_lakehouse.replay.scoring``).
        """
        ...


@dataclass(frozen=True)
class ReplayConfig:
    """What to replay.

    ``horizons`` count periods ahead of the latest period reported at the origin: horizon 1 is
    the period after it. ``evaluation_time`` is ``T_eval`` and must not be before the last
    origin.
    """

    series_id: str
    origins: Sequence[datetime]
    horizons: Sequence[int]
    evaluation_time: datetime
    step: timedelta = timedelta(days=7)
    min_history: int = 1

    def __post_init__(self) -> None:
        if not self.series_id.strip():
            raise ValueError("series_id must not be empty")
        if self.step <= timedelta(0):
            raise ValueError("step must be positive")
        if self.min_history < 1:
            raise ValueError("min_history must be at least 1")

        horizons = sorted(set(self.horizons))
        if not horizons or horizons[0] < 1:
            raise ValueError("horizons must be a non-empty set of positive integers")
        origins = sorted({to_utc(origin, "origin") for origin in self.origins})
        evaluation_time = to_utc(self.evaluation_time, "evaluation_time")
        if origins and evaluation_time < origins[-1]:
            raise ValueError(
                f"evaluation_time {evaluation_time.isoformat()} is before the last origin "
                f"{origins[-1].isoformat()}, so it would not be hindsight"
            )
        object.__setattr__(self, "horizons", tuple(horizons))
        object.__setattr__(self, "origins", tuple(origins))
        object.__setattr__(self, "evaluation_time", evaluation_time)


@dataclass(frozen=True)
class ScoredForecast:
    """One forecast of one target, made in one mode, and its scores."""

    model: str
    origin: datetime
    horizon: int
    target: date
    mode: Mode
    truth: float
    median: float
    wis: float
    abs_error: float
    coverage: tuple[tuple[float, bool], ...]


@dataclass(frozen=True)
class SkippedOrigin:
    """An origin that produced no forecasts, and why."""

    origin: datetime
    reason: str


@dataclass(frozen=True)
class ReplayResult:
    """Everything a replay produced, with the configuration that produced it."""

    config: ReplayConfig
    rows: list[ScoredForecast]
    skipped: list[SkippedOrigin]


def run_replay(
    store: AsOfStore, forecasters: Sequence[Forecaster], config: ReplayConfig
) -> ReplayResult:
    """Replay every origin for every forecaster, in real time and in hindsight."""
    names = [forecaster.name for forecaster in forecasters]
    if len(set(names)) != len(names):
        raise ValueError(f"forecaster names must be unique, got {names}")

    final = store.as_of(config.series_id, config.evaluation_time)
    truth = {observation.event_time: observation.value for observation in final}

    rows: list[ScoredForecast] = []
    skipped: list[SkippedOrigin] = []
    for origin in config.origins:
        known = store.as_of(config.series_id, origin)
        if len(known) < config.min_history:
            skipped.append(SkippedOrigin(origin, f"only {len(known)} periods known"))
            continue

        latest = known[-1].event_time
        scorable = [
            (horizon, latest + horizon * config.step)
            for horizon in config.horizons
            if latest + horizon * config.step in truth
        ]
        if not scorable:
            skipped.append(SkippedOrigin(origin, "no target has a value at the evaluation time"))
            continue

        targets = [target for _, target in scorable]
        histories = {
            Mode.REALTIME: tuple(known),
            Mode.HINDSIGHT: tuple(o for o in final if o.event_time <= latest),
        }
        for forecaster in forecasters:
            for mode, history in histories.items():
                predicted = forecaster.forecast(history, targets)
                for horizon, target in scorable:
                    rows.append(
                        _score(forecaster.name, origin, horizon, target, mode, predicted, truth)
                    )
    return ReplayResult(config=config, rows=rows, skipped=skipped)


def _score(
    model: str,
    origin: datetime,
    horizon: int,
    target: date,
    mode: Mode,
    predicted: Forecast,
    truth: Mapping[date, float],
) -> ScoredForecast:
    if target not in predicted:
        raise ValueError(f"{model} returned no forecast for {target}")
    quantiles = predicted[target]
    observed = truth[target]
    try:
        ordered = normalise_quantiles(quantiles)
    except ValueError as exc:
        raise ValueError(f"{model} gave an invalid forecast for {target}: {exc}") from exc
    return ScoredForecast(
        model=model,
        origin=origin,
        horizon=horizon,
        target=target,
        mode=mode,
        truth=observed,
        median=dict(ordered)[0.5],
        wis=weighted_interval_score(quantiles, observed),
        abs_error=absolute_error(quantiles, observed),
        coverage=tuple(sorted(central_coverage(quantiles, observed).items())),
    )
