"""Moving-block bootstrap over time-ordered units.

Forecast origins that are close in time are strongly correlated (one flu season is many
origins), so resampling single origins would give confidence intervals that are too narrow.
The moving-block bootstrap resamples *blocks* of consecutive origins instead, which keeps the
short-range dependence inside each block.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from vintage_lakehouse.forecast.series import empirical_quantile


def moving_block_indices(n: int, block_length: int, rng: random.Random) -> list[int]:
    """Resample ``range(n)`` in overlapping blocks of consecutive positions.

    Blocks start at a uniformly random position and are concatenated until ``n`` indices are
    collected, then truncated to ``n``. A block longer than ``n`` is shortened to ``n``.
    """
    if n < 1:
        raise ValueError("need at least one unit to resample")
    if block_length < 1:
        raise ValueError("block_length must be at least 1")
    length = min(block_length, n)
    indices: list[int] = []
    while len(indices) < n:
        start = rng.randrange(n - length + 1)
        indices.extend(range(start, start + length))
    return indices[:n]


def make_replicates(n: int, *, n_boot: int, block_length: int, seed: int) -> list[list[int]]:
    """Draw ``n_boot`` bootstrap resamples of ``range(n)``. The same seed gives the same draws."""
    if n_boot < 1:
        raise ValueError("n_boot must be at least 1")
    rng = random.Random(seed)
    return [moving_block_indices(n, block_length, rng) for _ in range(n_boot)]


def resample_counts(replicate: Sequence[int], n: int) -> list[int]:
    """How many times each of ``range(n)`` appears in a resample (its weight in the statistic)."""
    counts = [0] * n
    for index in replicate:
        counts[index] += 1
    return counts


def percentile_interval(samples: Sequence[float], level: float) -> tuple[float, float]:
    """Percentile confidence interval of bootstrap ``samples`` at confidence ``level``."""
    if not samples:
        raise ValueError("cannot take an interval of no samples")
    if not 0.0 < level < 1.0:
        raise ValueError("level must be between 0 and 1")
    ordered = sorted(samples)
    tail = (1.0 - level) / 2.0
    return empirical_quantile(ordered, tail), empirical_quantile(ordered, 1.0 - tail)
