"""Synthetic weekly series for the baseline forecaster tests."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone

from vintage_lakehouse.storage import Observation

UTC = timezone.utc
START = date(2023, 1, 1)  # a Sunday


def history_from_values(
    values: Sequence[float],
    *,
    start: date = START,
    step_days: int = 7,
    series: str = "s",
) -> list[Observation]:
    """One observation per value, each known five days after its period starts."""
    return [
        Observation(
            series_id=series,
            event_time=start + timedelta(days=i * step_days),
            knowledge_time=datetime.combine(
                start + timedelta(days=i * step_days + 5), time.min, tzinfo=UTC
            ),
            value=float(value),
            source="test",
        )
        for i, value in enumerate(values)
    ]


def seasonal_values(
    n: int, *, period: int = 52, base: float = 5.0, amplitude: float = 3.0
) -> list[float]:
    """An exactly periodic series: copies of one pattern, so every seasonal error is 0."""
    pattern = [
        round(base + amplitude * math.sin(2 * math.pi * i / period), 6) for i in range(period)
    ]
    return [pattern[i % period] for i in range(n)]


def with_noise(values: Sequence[float], sd: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    return [value + rng.gauss(0.0, sd) for value in values]


def target_dates(history: Sequence[Observation], horizons: Sequence[int]) -> list[date]:
    last = history[-1].event_time
    return [last + timedelta(weeks=h) for h in horizons]
