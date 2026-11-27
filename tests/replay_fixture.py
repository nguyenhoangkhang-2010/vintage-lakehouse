"""A flat series whose first release is always too low, and simple forecasters for it.

Every week is published twice: first as 9.0 (five days after the week starts), then revised to
10.0 (twelve days after). A forecaster that repeats the last value it sees therefore predicts
9.0 in real time (the last week is still provisional) but 10.0 in hindsight, and the truth is
always 10.0. With a half-width of 0.5 around the median the scores can be computed by hand:

- real time: quantiles 8.5 / 9.0 / 9.5, WIS = (0.5 * 1 + 0.25 * (1 + 4 * 0.5)) / 1.5 = 5 / 6
- hindsight: quantiles 9.5 / 10.0 / 10.5, WIS = (0 + 0.25 * 1) / 1.5 = 1 / 6
- leakage gap = (5/6 - 1/6) / (5/6) = 0.8
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from vintage_lakehouse.replay import Forecast, ReplayConfig
from vintage_lakehouse.storage import InMemoryStore, Observation

UTC = timezone.utc
BASE = date(2023, 1, 1)  # a Sunday
SERIES = "flat"
EVAL = datetime(2024, 1, 1, tzinfo=UTC)


def week(n: int) -> date:
    return BASE + timedelta(weeks=n)


def at(day: date, extra_days: int) -> datetime:
    return datetime.combine(day + timedelta(days=extra_days), time.min, tzinfo=UTC)


def flat_series(n_weeks: int = 12) -> list[Observation]:
    out: list[Observation] = []
    for w in range(n_weeks):
        out.append(Observation(SERIES, week(w), at(week(w), 5), 9.0, "test"))
        out.append(Observation(SERIES, week(w), at(week(w), 12), 10.0, "test"))
    return out


def origin(w: int) -> datetime:
    """Just after week ``w`` is first published, and after week ``w - 1`` has been revised."""
    return at(week(w), 6)


def make_store(observations: list[Observation] | None = None) -> InMemoryStore:
    store = InMemoryStore()
    store.append(flat_series() if observations is None else observations)
    return store


def config(**overrides: object) -> ReplayConfig:
    fields: dict[str, object] = {
        "series_id": SERIES,
        "origins": [origin(w) for w in range(12)],
        "horizons": [1, 2],
        "evaluation_time": EVAL,
    }
    fields.update(overrides)
    return ReplayConfig(**fields)  # type: ignore[arg-type]


@dataclass
class LastValue:
    """Median = the last value in the history; fixed-width quantiles around it."""

    name: str = "last_value"
    half_width: float = 0.5
    calls: list[tuple[tuple[Observation, ...], tuple[date, ...]]] = field(default_factory=list)

    def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
        self.calls.append((tuple(history), tuple(targets)))
        m = history[-1].value
        quantiles = {0.25: m - self.half_width, 0.5: m, 0.75: m + self.half_width}
        return {target: quantiles for target in targets}


@dataclass
class Constant:
    name: str
    value: float

    def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
        return {t: {0.25: self.value, 0.5: self.value, 0.75: self.value} for t in targets}
