"""Summaries of a replay: mean scores, and the leakage gap between hindsight and real time.

The **leakage gap** is the relative improvement in mean WIS when a forecaster is given
hindsight data instead of real-time data::

    gap = (WIS_realtime - WIS_hindsight) / WIS_realtime

A positive gap means that evaluating on revised data makes the forecaster look better than it
would have been in real time. Only matched pairs count: the same model, origin and horizon in
both modes. This module gives point estimates; confidence intervals come with the experiment.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from vintage_lakehouse.replay.harness import Mode, ScoredForecast


@dataclass(frozen=True)
class ModelSummary:
    """Mean scores of one model at one horizon in one mode."""

    model: str
    horizon: int
    mode: Mode
    n: int
    mean_wis: float
    mean_abs_error: float
    coverage: tuple[tuple[float, float], ...]  # nominal level -> share of targets inside


@dataclass(frozen=True)
class LeakageGap:
    """How much better a model looks in hindsight than in real time.

    ``horizon`` is ``None`` for the figure pooled over all horizons. ``gap`` is ``None`` when
    the real-time WIS is zero, where a relative difference is undefined.
    """

    model: str
    horizon: int | None
    n_pairs: int
    wis_realtime: float
    wis_hindsight: float
    gap: float | None


def summarize(rows: Iterable[ScoredForecast]) -> list[ModelSummary]:
    """Mean WIS, mean absolute error and coverage per (model, horizon, mode)."""
    groups: dict[tuple[str, int, Mode], list[ScoredForecast]] = defaultdict(list)
    for row in rows:
        groups[(row.model, row.horizon, row.mode)].append(row)

    summaries: list[ModelSummary] = []
    for (model, horizon, mode), group in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2].value)
    ):
        inside: dict[float, list[bool]] = defaultdict(list)
        for row in group:
            for level, covered in row.coverage:
                inside[level].append(covered)
        summaries.append(
            ModelSummary(
                model=model,
                horizon=horizon,
                mode=mode,
                n=len(group),
                mean_wis=math.fsum(r.wis for r in group) / len(group),
                mean_abs_error=math.fsum(r.abs_error for r in group) / len(group),
                coverage=tuple(
                    (level, sum(flags) / len(flags)) for level, flags in sorted(inside.items())
                ),
            )
        )
    return summaries


def _relative_gap(realtime: float, hindsight: float) -> float | None:
    return None if realtime == 0.0 else (realtime - hindsight) / realtime


def leakage_gap(rows: Iterable[ScoredForecast]) -> list[LeakageGap]:
    """Leakage gap per (model, horizon), then pooled over horizons for each model.

    The result is ordered by model, with each model's horizons in ascending order followed by
    its pooled figure (``horizon=None``).
    """
    pairs: dict[tuple[str, int], dict[tuple[datetime, Mode], ScoredForecast]] = defaultdict(dict)
    for row in rows:
        pairs[(row.model, row.horizon)][(row.origin, row.mode)] = row

    matched: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    for key, by_origin_mode in pairs.items():
        origins = sorted({origin for origin, _ in by_origin_mode})
        for origin in origins:
            realtime = by_origin_mode.get((origin, Mode.REALTIME))
            hindsight = by_origin_mode.get((origin, Mode.HINDSIGHT))
            if realtime is not None and hindsight is not None:
                matched[key].append((realtime.wis, hindsight.wis))

    def build(model: str, horizon: int | None, scores: list[tuple[float, float]]) -> LeakageGap:
        realtime = math.fsum(r for r, _ in scores) / len(scores)
        hindsight = math.fsum(h for _, h in scores) / len(scores)
        return LeakageGap(
            model=model,
            horizon=horizon,
            n_pairs=len(scores),
            wis_realtime=realtime,
            wis_hindsight=hindsight,
            gap=_relative_gap(realtime, hindsight),
        )

    results: list[LeakageGap] = []
    for model in sorted({model for model, _ in matched}):
        pooled: list[tuple[float, float]] = []
        for horizon in sorted(h for m, h in matched if m == model):
            scores = matched[(model, horizon)]
            results.append(build(model, horizon, scores))
            pooled.extend(scores)
        results.append(build(model, None, pooled))
    return results
