"""Timing helpers and summaries."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from vintage_lakehouse.forecast.series import empirical_quantile


@dataclass(frozen=True)
class TimingSummary:
    """Seconds: number of samples, extremes, mean, median and 95th percentile."""

    n: int
    min: float
    median: float
    p95: float
    max: float
    mean: float


def summarize_times(samples: Sequence[float]) -> TimingSummary:
    """Summarise timings in seconds."""
    if not samples:
        raise ValueError("no timings to summarise")
    ordered = sorted(samples)
    return TimingSummary(
        n=len(ordered),
        min=ordered[0],
        median=empirical_quantile(ordered, 0.5),
        p95=empirical_quantile(ordered, 0.95),
        max=ordered[-1],
        mean=sum(ordered) / len(ordered),
    )


def measure(fn: Callable[[], object], *, repeats: int, warmup: int = 0) -> list[float]:
    """Call ``fn`` ``warmup`` times unrecorded, then ``repeats`` times, timing each call."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    if warmup < 0:
        raise ValueError("warmup must not be negative")
    for _ in range(warmup):
        fn()
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - started)
    return samples
