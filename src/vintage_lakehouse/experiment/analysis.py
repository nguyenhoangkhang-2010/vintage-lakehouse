"""Statistics for the leakage-gap experiment.

Everything here works on matched pairs: the same model, origin and horizon scored both in real
time and in hindsight. For a set of pairs

    gap          = (sum WIS_realtime - sum WIS_hindsight) / sum WIS_realtime
    degradation  = mean (WIS_realtime - WIS_hindsight)

The gap is the relative improvement from hindsight, the degradation the same in WIS units.
Confidence intervals come from a block bootstrap over origins: each replicate resamples origins
(in blocks) and every model, horizon and statistic is recomputed on that same resample, so
comparisons between models keep their correlation.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from vintage_lakehouse.experiment.bootstrap import (
    make_replicates,
    percentile_interval,
    resample_counts,
)
from vintage_lakehouse.replay.harness import Mode, ScoredForecast

_Key = tuple[str, int | None]  # (model, horizon); horizon None means pooled over horizons


@dataclass(frozen=True)
class GapEstimate:
    """Leakage gap of one model at one horizon (or pooled), with bootstrap intervals."""

    model: str
    horizon: int | None
    n_pairs: int
    wis_realtime: float
    wis_hindsight: float
    gap: float | None
    gap_low: float | None
    gap_high: float | None
    degradation: float
    degradation_low: float
    degradation_high: float


@dataclass(frozen=True)
class GapComparison:
    """Difference between two models' pooled gaps (model_a minus model_b)."""

    model_a: str
    model_b: str
    difference: float
    low: float
    high: float
    n_valid: int


@dataclass(frozen=True)
class Analysis:
    """Estimates and comparisons for one scenario."""

    n_origins: int
    estimates: list[GapEstimate]
    comparisons: list[GapComparison]


@dataclass(frozen=True)
class RevisionGroup:
    """Leakage gap among the origins in one group of recent revision size."""

    model: str
    group: int
    n_origins: int
    revision_low: float
    revision_high: float
    gap: float | None


@dataclass(frozen=True)
class RevisionCorrelation:
    """Spearman correlation between recent revision size and the per-origin WIS degradation."""

    model: str
    n_origins: int
    spearman: float | None
    low: float | None
    high: float | None


class _Sums:
    """Per-origin sums of WIS over the matched pairs at that origin."""

    def __init__(self, n_origins: int) -> None:
        self.realtime = [0.0] * n_origins
        self.hindsight = [0.0] * n_origins
        self.pairs = [0] * n_origins


def _collect(rows: Sequence[ScoredForecast]) -> tuple[list[datetime], list[str], dict[_Key, _Sums]]:
    """Group rows into matched pairs and sum them per origin, per horizon and pooled."""
    origins = sorted({row.origin for row in rows})
    position = {origin: i for i, origin in enumerate(origins)}
    models: list[str] = []
    cells: dict[tuple[str, datetime, int], dict[Mode, float]] = defaultdict(dict)
    for row in rows:
        if row.model not in models:
            models.append(row.model)
        cells[(row.model, row.origin, row.horizon)][row.mode] = row.wis

    sums: dict[_Key, _Sums] = {}
    for (model, origin, horizon), by_mode in cells.items():
        if Mode.REALTIME not in by_mode or Mode.HINDSIGHT not in by_mode:
            continue
        i = position[origin]
        for key in ((model, horizon), (model, None)):
            cell = sums.setdefault(key, _Sums(len(origins)))
            cell.realtime[i] += by_mode[Mode.REALTIME]
            cell.hindsight[i] += by_mode[Mode.HINDSIGHT]
            cell.pairs[i] += 1
    return origins, models, sums


def _ratio(realtime: float, hindsight: float) -> float | None:
    return None if realtime == 0.0 else (realtime - hindsight) / realtime


def _weighted(cell: _Sums, weights: Sequence[int]) -> tuple[float, float, int]:
    realtime = hindsight = 0.0
    pairs = 0
    for w, r, h, p in zip(weights, cell.realtime, cell.hindsight, cell.pairs, strict=True):
        if w:
            realtime += w * r
            hindsight += w * h
            pairs += w * p
    return realtime, hindsight, pairs


def analyze(
    rows: Sequence[ScoredForecast], *, replicates: Sequence[Sequence[int]], level: float = 0.95
) -> Analysis:
    """Estimate the gap per model and horizon, pooled per model, and compare models.

    ``replicates`` are bootstrap resamples of the origins (see ``make_replicates``) over the
    sorted distinct origins of ``rows``.
    """
    origins, models, sums = _collect(rows)
    n = len(origins)
    if n == 0:
        return Analysis(0, [], [])
    if any(index >= n or index < 0 for replicate in replicates for index in replicate):
        raise ValueError("bootstrap replicates refer to origins that do not exist")
    weight_vectors = [resample_counts(replicate, n) for replicate in replicates]

    gap_samples: dict[_Key, list[float | None]] = {}
    degradation_samples: dict[_Key, list[float]] = {}
    for key, cell in sums.items():
        gaps: list[float | None] = []
        degradations: list[float] = []
        for weights in weight_vectors:
            realtime, hindsight, pairs = _weighted(cell, weights)
            gaps.append(_ratio(realtime, hindsight))
            if pairs:
                degradations.append((realtime - hindsight) / pairs)
        gap_samples[key] = gaps
        degradation_samples[key] = degradations

    estimates: list[GapEstimate] = []
    for model in models:
        horizons = sorted({h for m, h in sums if m == model and h is not None})
        for horizon in [*horizons, None]:
            key = (model, horizon)
            pair_sums = sums.get(key)
            if pair_sums is None:
                continue
            realtime, hindsight = sum(pair_sums.realtime), sum(pair_sums.hindsight)
            pairs = sum(pair_sums.pairs)
            valid_gaps = [g for g in gap_samples[key] if g is not None]
            gap_low, gap_high = (
                percentile_interval(valid_gaps, level) if valid_gaps else (None, None)
            )
            deg_low, deg_high = (
                percentile_interval(degradation_samples[key], level)
                if degradation_samples[key]
                else (math.nan, math.nan)
            )
            estimates.append(
                GapEstimate(
                    model=model,
                    horizon=horizon,
                    n_pairs=pairs,
                    wis_realtime=realtime / pairs,
                    wis_hindsight=hindsight / pairs,
                    gap=_ratio(realtime, hindsight),
                    gap_low=gap_low,
                    gap_high=gap_high,
                    degradation=(realtime - hindsight) / pairs,
                    degradation_low=deg_low,
                    degradation_high=deg_high,
                )
            )

    comparisons: list[GapComparison] = []
    for i, model_a in enumerate(models):
        for model_b in models[i + 1 :]:
            key_a, key_b = (model_a, None), (model_b, None)
            if key_a not in sums or key_b not in sums:
                continue
            point_a = _ratio(sum(sums[key_a].realtime), sum(sums[key_a].hindsight))
            point_b = _ratio(sum(sums[key_b].realtime), sum(sums[key_b].hindsight))
            if point_a is None or point_b is None:
                continue
            differences = [
                a - b
                for a, b in zip(gap_samples[key_a], gap_samples[key_b], strict=True)
                if a is not None and b is not None
            ]
            if not differences:
                continue
            low, high = percentile_interval(differences, level)
            comparisons.append(
                GapComparison(model_a, model_b, point_a - point_b, low, high, len(differences))
            )
    return Analysis(n_origins=n, estimates=estimates, comparisons=comparisons)


def analyze_rows(
    rows: Sequence[ScoredForecast],
    *,
    n_boot: int,
    block_length: int,
    level: float = 0.95,
    seed: int = 0,
) -> Analysis:
    """``analyze`` with replicates drawn from ``seed``."""
    n = len({row.origin for row in rows})
    if n == 0:
        return Analysis(0, [], [])
    replicates = make_replicates(n, n_boot=n_boot, block_length=block_length, seed=seed)
    return analyze(rows, replicates=replicates, level=level)


# --- does the gap grow with the size of recent revisions? --------------------------------


def _ranks(values: Sequence[float]) -> list[float]:
    """Ranks starting at 1, with the average rank for ties."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        average = (start + end) / 2.0 + 1.0
        for position in range(start, end + 1):
            ranks[order[position]] = average
        start = end + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Spearman rank correlation, or ``None`` when either variable is constant."""
    if len(x) != len(y):
        raise ValueError("x and y must have the same length")
    if len(x) < 2:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mean_x, mean_y = sum(rx) / len(rx), sum(ry) / len(ry)
    covariance = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry, strict=True))
    spread_x = math.sqrt(sum((a - mean_x) ** 2 for a in rx))
    spread_y = math.sqrt(sum((b - mean_y) ** 2 for b in ry))
    if spread_x == 0.0 or spread_y == 0.0:
        return None
    return covariance / (spread_x * spread_y)


def revision_analysis(
    rows: Sequence[ScoredForecast],
    revision_size: Mapping[datetime, float],
    *,
    replicates: Sequence[Sequence[int]],
    level: float = 0.95,
    groups: int = 3,
) -> tuple[list[RevisionGroup], list[RevisionCorrelation]]:
    """Relate the gap to the size of the revisions that had not yet been applied at each origin.

    ``revision_size`` maps an origin to how much, on average, the latest reported periods were
    later revised. Origins are split into ``groups`` of equal size by that figure, and the
    Spearman correlation between it and each origin's mean WIS degradation is bootstrapped.
    """
    origins, models, sums = _collect(rows)
    n = len(origins)
    group_results: list[RevisionGroup] = []
    correlations: list[RevisionCorrelation] = []
    if n == 0:
        return group_results, correlations

    for model in models:
        cell = sums.get((model, None))
        if cell is None:
            continue
        usable = [i for i in range(n) if cell.pairs[i] and origins[i] in revision_size]
        usable.sort(key=lambda i: revision_size[origins[i]])
        x = {i: revision_size[origins[i]] for i in usable}
        y = {i: (cell.realtime[i] - cell.hindsight[i]) / cell.pairs[i] for i in usable}

        size = len(usable)
        for g in range(groups):
            members = usable[g * size // groups : (g + 1) * size // groups]
            if not members:
                continue
            realtime = sum(cell.realtime[i] for i in members)
            hindsight = sum(cell.hindsight[i] for i in members)
            group_results.append(
                RevisionGroup(
                    model=model,
                    group=g + 1,
                    n_origins=len(members),
                    revision_low=x[members[0]],
                    revision_high=x[members[-1]],
                    gap=_ratio(realtime, hindsight),
                )
            )

        point = spearman([x[i] for i in usable], [y[i] for i in usable])
        samples: list[float] = []
        for replicate in replicates:
            chosen = [i for i in replicate if i in x]
            value = spearman([x[i] for i in chosen], [y[i] for i in chosen])
            if value is not None:
                samples.append(value)
        low, high = percentile_interval(samples, level) if samples else (None, None)
        correlations.append(RevisionCorrelation(model, size, point, low, high))
    return group_results, correlations
