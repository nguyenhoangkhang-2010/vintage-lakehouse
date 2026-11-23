"""Helpers shared by the baseline forecasters.

The baselines work on a regular series (one value per fixed step), build quantile forecasts on a
common set of levels, and share the same rules for gaps and quantile ordering.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import NormalDist

from vintage_lakehouse.replay.scoring import normalise_quantiles
from vintage_lakehouse.storage.model import Observation

#: Quantile levels every baseline produces, so that their scores are comparable.
DEFAULT_LEVELS = (0.025, 0.1, 0.25, 0.5, 0.75, 0.9, 0.975)


class InsufficientHistory(ValueError):
    """The history is too short for this forecaster."""


@dataclass(frozen=True)
class RegularSeries:
    """Values at a fixed step, starting at ``start``."""

    start: date
    step_days: int
    values: tuple[float, ...]

    @property
    def n(self) -> int:
        """Number of periods."""
        return len(self.values)

    @property
    def last_event(self) -> date:
        """The event_time of the last period."""
        return self.date_at(self.n - 1)

    def date_at(self, index: int) -> date:
        """The event_time of the period at ``index`` (which may lie beyond the last period)."""
        return self.start + timedelta(days=index * self.step_days)


def regularize(history: Sequence[Observation], step_days: int = 7) -> RegularSeries:
    """Turn a history into a regular series.

    The history must be ordered by event_time with no duplicates, and every event_time must be a
    whole number of steps from the first. A missing period is filled with the value before it.
    """
    if not history:
        raise InsufficientHistory("the history is empty")
    values: list[float] = []
    start = history[0].event_time
    previous: date | None = None
    for observation in history:
        if previous is not None and observation.event_time <= previous:
            raise ValueError("the history must be ordered by event_time without duplicates")
        previous = observation.event_time
        offset = (observation.event_time - start).days
        if offset % step_days:
            raise ValueError(
                f"event_time {observation.event_time} is not a multiple of {step_days} days "
                f"after {start}"
            )
        index = offset // step_days
        while len(values) < index:
            values.append(values[-1])
        values.append(observation.value)
    return RegularSeries(start=start, step_days=step_days, values=tuple(values))


def steps_ahead(series: RegularSeries, target: date) -> int:
    """How many steps after the last period ``target`` lies (at least 1)."""
    days = (target - series.last_event).days
    if days <= 0 or days % series.step_days:
        raise ValueError(
            f"target {target} must be a positive whole number of {series.step_days}-day steps "
            f"after the last period {series.last_event}"
        )
    return days // series.step_days


def empirical_quantile(sorted_values: Sequence[float], level: float) -> float:
    """Quantile of sorted values by linear interpolation (the common "type 7" definition)."""
    if not sorted_values:
        raise ValueError("cannot take a quantile of no values")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = level * (len(sorted_values) - 1)
    low = math.floor(position)
    high = min(low + 1, len(sorted_values) - 1)
    fraction = position - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * fraction


def normal_quantiles(mean: float, sd: float, levels: Sequence[float]) -> dict[float, float]:
    """Quantiles of a normal distribution. A zero spread collapses them onto the mean."""
    if not (math.isfinite(mean) and math.isfinite(sd)):
        raise ValueError(f"forecast mean and spread must be finite, got {mean!r} and {sd!r}")
    if sd <= 0.0:
        return {level: mean for level in levels}
    dist = NormalDist(mean, sd)
    return {level: dist.inv_cdf(level) for level in levels}


def finish(quantiles: Mapping[float, float], lower_bound: float | None) -> dict[float, float]:
    """Put quantiles in non-decreasing order, and apply an optional lower bound.

    Sorting the values across levels removes any crossing, which is the standard "rearrangement"
    fix. A lower bound (for example 0 for a rate) can only move a forecast towards a plausible
    value.
    """
    levels = sorted(quantiles)
    values = sorted(quantiles[level] for level in levels)
    if lower_bound is not None:
        values = [max(value, lower_bound) for value in values]
    return dict(zip(levels, values, strict=True))


def check_levels(levels: Sequence[float]) -> tuple[float, ...]:
    """Validate quantile levels (median present, symmetric) and return them sorted."""
    return tuple(level for level, _ in normalise_quantiles(dict.fromkeys(levels, 0.0)))
