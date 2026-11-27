"""The backfill-recovery experiment: how much of the leakage gap does a correction recover?

For each base model the experiment makes three kinds of forecast on the same origins and targets
(ADR 0010):

- the base model in real time (``a``) and in hindsight (``b``), as in the leakage-gap experiment;
- the base model behind a backfill correction, in real time (``c``), for each variant.

With matched triples (same model, origin and horizon):

    gap           = sum(a - b)            what hindsight data would buy
    improvement   = sum(a - c)            what the correction buys
    recovery      = improvement / gap     the share of the gap recovered

Confidence intervals come from the same moving-block bootstrap over origins as the leakage-gap
experiment, so every quantity is recomputed on one shared resample.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from vintage_lakehouse.backfill import (
    BackfillCorrected,
    CorrectionConfig,
    CorrectionFactors,
    VintageTable,
    estimate_correction,
)
from vintage_lakehouse.experiment.analysis import GapEstimate, analyze
from vintage_lakehouse.experiment.bootstrap import (
    make_replicates,
    percentile_interval,
    resample_counts,
)
from vintage_lakehouse.experiment.runner import ExperimentConfig, Scenario, make_forecasters
from vintage_lakehouse.replay import (
    Forecaster,
    Mode,
    ReplayConfig,
    ScoredForecast,
    SkippedOrigin,
    run_replay,
)
from vintage_lakehouse.storage.base import AsOfStore

#: The variants of ADR 0010, primary first.
VARIANTS = ("backfill", "backfill-additive", "backfill-placebo")

_Key = tuple[str, str, int | None]  # (model, variant, horizon); horizon None means pooled


def variant_config(variant: str, base: CorrectionConfig) -> CorrectionConfig:
    """The correction settings of a named variant, derived from the shared ``base`` settings."""
    if variant == "backfill":
        return replace(base, kind="ratio", reverse_lags=False)
    if variant == "backfill-additive":
        return replace(base, kind="additive", reverse_lags=False)
    if variant == "backfill-placebo":
        return replace(base, kind="ratio", reverse_lags=True)
    raise ValueError(f"unknown variant {variant!r}; choose from {VARIANTS}")


@dataclass(frozen=True)
class RecoveryConfig:
    """Everything that defines a run: the experiment design plus the correction settings."""

    experiment: ExperimentConfig = field(default_factory=ExperimentConfig)
    variants: tuple[str, ...] = VARIANTS
    correction: CorrectionConfig = field(default_factory=CorrectionConfig)

    def __post_init__(self) -> None:
        if not self.variants or len(set(self.variants)) != len(self.variants):
            raise ValueError(f"variants must be unique names from {VARIANTS}, got {self.variants}")
        for variant in self.variants:
            variant_config(variant, self.correction)  # rejects unknown names
        if self.correction.step_days != self.experiment.step_days:
            raise ValueError("the correction and the experiment must use the same step_days")


@dataclass(frozen=True)
class RecoveryResult:
    """The scores of a run, and the correction it estimated at every origin."""

    config: RecoveryConfig
    base_rows: list[ScoredForecast]
    corrected_rows: list[ScoredForecast]
    skipped: list[SkippedOrigin]
    factors_by_origin: dict[datetime, CorrectionFactors]
    seconds: float


def corrected_name(model: str, variant: str) -> str:
    """The name under which a corrected forecaster is scored."""
    return f"{model}+{variant}"


def run_recovery(
    store: AsOfStore,
    config: RecoveryConfig,
    *,
    forecasters: Sequence[Forecaster] | None = None,
    progress: Callable[[str], None] | None = None,
) -> RecoveryResult:
    """Replay the base models, then the corrected models in real time, on the same origins."""
    say = progress or (lambda message: None)
    experiment = config.experiment
    bases = (
        list(forecasters)
        if forecasters is not None
        else make_forecasters(experiment.models, experiment.lower_bound)
    )
    started = time.perf_counter()
    table = VintageTable.from_store(store, experiment.series_id, experiment.evaluation_time)
    corrected: list[Forecaster] = [
        BackfillCorrected(
            base,
            table,
            variant_config(variant, config.correction),
            name=corrected_name(base.name, variant),
        )
        for base in bases
        for variant in config.variants
    ]
    min_history = max((getattr(f, "min_history", 1) for f in bases), default=1)
    origins = experiment.origins()

    def replay_config(modes: tuple[Mode, ...]) -> ReplayConfig:
        return ReplayConfig(
            series_id=experiment.series_id,
            origins=origins,
            horizons=experiment.horizons,
            evaluation_time=experiment.evaluation_time,
            step=timedelta(days=experiment.step_days),
            min_history=min_history,
            modes=modes,
        )

    say(f"replaying {len(bases)} base models in real time and hindsight ({len(origins)} origins)")
    base_result = run_replay(store, bases, replay_config((Mode.REALTIME, Mode.HINDSIGHT)))
    say(
        f"replaying {len(corrected)} corrected models in real time "
        f"({len(config.variants)} variants x {len(bases)} models)"
    )
    corrected_result = run_replay(store, corrected, replay_config((Mode.REALTIME,)))

    primary = variant_config(config.variants[0], config.correction)
    factors: dict[datetime, CorrectionFactors] = {}
    for origin in origins:
        known = store.as_of(experiment.series_id, origin)
        if known:
            cutoff = max(o.knowledge_time for o in known)
            factors[origin] = estimate_correction(table, cutoff, primary)
    return RecoveryResult(
        config=config,
        base_rows=base_result.rows,
        corrected_rows=corrected_result.rows,
        skipped=base_result.skipped,
        factors_by_origin=factors,
        seconds=time.perf_counter() - started,
    )


# --- statistics ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryEstimate:
    """What one variant of the correction buys one model, at one horizon or pooled.

    ``recovery`` is ``None`` when it was not computed (the model's own gap interval does not
    exclude zero) or when the point estimate has no positive gap to divide by.
    """

    model: str
    variant: str
    horizon: int | None
    n_pairs: int
    wis_realtime: float
    wis_hindsight: float
    wis_corrected: float
    improvement: float
    improvement_low: float
    improvement_high: float
    relative_improvement: float | None
    relative_low: float | None
    relative_high: float | None
    recovery: float | None
    recovery_low: float | None
    recovery_high: float | None
    recovery_valid: int


@dataclass(frozen=True)
class VariantComparison:
    """Difference between the mean improvements of two variants (``variant`` minus ``versus``)."""

    model: str
    variant: str
    versus: str
    difference: float
    low: float
    high: float


@dataclass(frozen=True)
class RecoveryAnalysis:
    """Estimates for one scenario."""

    n_origins: int
    gaps: dict[str, GapEstimate]
    estimates: list[RecoveryEstimate]
    comparisons: list[VariantComparison]


class _Cell:
    """Per-origin sums of the three WIS values over the matched triples at that origin."""

    def __init__(self, n: int) -> None:
        self.base = [0.0] * n
        self.hindsight = [0.0] * n
        self.corrected = [0.0] * n
        self.pairs = [0] * n


def _split(name: str) -> tuple[str, str]:
    model, _, variant = name.rpartition("+")
    return model, variant


def _collect(
    base_rows: Sequence[ScoredForecast], corrected_rows: Sequence[ScoredForecast]
) -> tuple[list[datetime], dict[_Key, _Cell]]:
    origins = sorted({row.origin for row in base_rows})
    position = {origin: i for i, origin in enumerate(origins)}
    base: dict[tuple[str, datetime, int], dict[Mode, float]] = defaultdict(dict)
    for row in base_rows:
        base[(row.model, row.origin, row.horizon)][row.mode] = row.wis

    cells: dict[_Key, _Cell] = {}
    for row in corrected_rows:
        if row.mode is not Mode.REALTIME or row.origin not in position:
            continue
        model, variant = _split(row.model)
        by_mode = base.get((model, row.origin, row.horizon), {})
        if Mode.REALTIME not in by_mode or Mode.HINDSIGHT not in by_mode:
            continue
        i = position[row.origin]
        for key in ((model, variant, row.horizon), (model, variant, None)):
            cell = cells.setdefault(key, _Cell(len(origins)))
            cell.base[i] += by_mode[Mode.REALTIME]
            cell.hindsight[i] += by_mode[Mode.HINDSIGHT]
            cell.corrected[i] += row.wis
            cell.pairs[i] += 1
    return origins, cells


def _totals(cell: _Cell, weights: Sequence[int]) -> tuple[float, float, float, int]:
    base = hindsight = corrected = 0.0
    pairs = 0
    for w, a, b, c, p in zip(
        weights, cell.base, cell.hindsight, cell.corrected, cell.pairs, strict=True
    ):
        if w:
            base += w * a
            hindsight += w * b
            corrected += w * c
            pairs += w * p
    return base, hindsight, corrected, pairs


def _interval(samples: Sequence[float], level: float) -> tuple[float, float] | None:
    return percentile_interval(samples, level) if samples else None


def analyze_recovery(
    base_rows: Sequence[ScoredForecast],
    corrected_rows: Sequence[ScoredForecast],
    *,
    replicates: Sequence[Sequence[int]],
    level: float = 0.95,
) -> RecoveryAnalysis:
    """Estimate improvement and recovery for every model, variant and horizon (and pooled).

    ``base_rows`` hold both modes of the base models, ``corrected_rows`` the real-time scores of
    ``<model>+<variant>`` forecasters. ``replicates`` are bootstrap resamples over the sorted
    distinct origins of ``base_rows``. Recovery is computed only for models whose pooled gap
    interval excludes zero (ADR 0010); the improvement is always given.
    """
    origins, cells = _collect(base_rows, corrected_rows)
    n = len(origins)
    if n == 0:
        return RecoveryAnalysis(0, {}, [], [])
    if any(index >= n or index < 0 for replicate in replicates for index in replicate):
        raise ValueError("bootstrap replicates refer to origins that do not exist")
    weight_vectors = [resample_counts(replicate, n) for replicate in replicates]

    gaps = {
        e.model: e
        for e in analyze(base_rows, replicates=replicates, level=level).estimates
        if e.horizon is None
    }
    eligible = {m for m, g in gaps.items() if g.gap_low is not None and g.gap_low > 0.0}

    improvement_samples: dict[_Key, list[float]] = {}
    estimates: list[RecoveryEstimate] = []
    for key in sorted(cells, key=lambda k: (k[0], k[1], -1 if k[2] is None else k[2])):
        model, variant, horizon = key
        cell = cells[key]
        improvements: list[float] = []
        relatives: list[float] = []
        recoveries: list[float] = []
        for weights in weight_vectors:
            base, hindsight, corrected, pairs = _totals(cell, weights)
            if not pairs:
                continue
            improvements.append((base - corrected) / pairs)
            if base > 0.0:
                relatives.append((base - corrected) / base)
            if base - hindsight > 0.0:
                recoveries.append((base - corrected) / (base - hindsight))
        improvement_samples[key] = improvements

        point_weights = [1] * n
        base, hindsight, corrected, pairs = _totals(cell, point_weights)
        improvement = (base - corrected) / pairs
        improvement_ci = _interval(improvements, level) or (math.nan, math.nan)
        relative_ci = _interval(relatives, level)
        report_recovery = model in eligible and base - hindsight > 0.0
        recovery_ci = _interval(recoveries, level) if report_recovery else None
        estimates.append(
            RecoveryEstimate(
                model=model,
                variant=variant,
                horizon=horizon,
                n_pairs=pairs,
                wis_realtime=base / pairs,
                wis_hindsight=hindsight / pairs,
                wis_corrected=corrected / pairs,
                improvement=improvement,
                improvement_low=improvement_ci[0],
                improvement_high=improvement_ci[1],
                relative_improvement=(base - corrected) / base if base > 0.0 else None,
                relative_low=relative_ci[0] if relative_ci else None,
                relative_high=relative_ci[1] if relative_ci else None,
                recovery=(base - corrected) / (base - hindsight) if report_recovery else None,
                recovery_low=recovery_ci[0] if recovery_ci else None,
                recovery_high=recovery_ci[1] if recovery_ci else None,
                recovery_valid=len(recoveries) if report_recovery else 0,
            )
        )

    comparisons: list[VariantComparison] = []
    models = sorted({m for m, _, _ in cells})
    for model in models:
        variants = [v for m, v, h in cells if m == model and h is None]
        if not variants:
            continue
        reference = sorted(variants, key=lambda v: VARIANTS.index(v) if v in VARIANTS else 99)[0]
        for variant in variants:
            if variant == reference:
                continue
            a, b = (
                improvement_samples[(model, variant, None)],
                improvement_samples[(model, reference, None)],
            )
            if len(a) != len(b) or not a:
                continue
            pooled_a = next(
                e.improvement
                for e in estimates
                if (e.model, e.variant, e.horizon) == (model, variant, None)
            )
            pooled_b = next(
                e.improvement
                for e in estimates
                if (e.model, e.variant, e.horizon) == (model, reference, None)
            )
            low, high = percentile_interval([x - y for x, y in zip(a, b, strict=True)], level)
            comparisons.append(
                VariantComparison(model, variant, reference, pooled_a - pooled_b, low, high)
            )
    return RecoveryAnalysis(n, gaps, estimates, comparisons)


def run_recovery_analyses(result: RecoveryResult) -> dict[Scenario, RecoveryAnalysis]:
    """Analyse the run for all origins and again without the excluded origins."""
    experiment = result.config.experiment
    scenarios = [Scenario(experiment.evaluation_time)]
    if experiment.exclusions:
        scenarios.append(Scenario(experiment.evaluation_time, experiment.exclusions))
    analyses: dict[Scenario, RecoveryAnalysis] = {}
    for scenario in scenarios:
        base = [r for r in result.base_rows if scenario.keeps(r.origin)]
        corrected = [r for r in result.corrected_rows if scenario.keeps(r.origin)]
        n = len({row.origin for row in base})
        replicates = (
            make_replicates(
                n,
                n_boot=experiment.n_boot,
                block_length=experiment.block_length(),
                seed=experiment.seed,
            )
            if n
            else []
        )
        analyses[scenario] = analyze_recovery(
            base, corrected, replicates=replicates, level=experiment.confidence
        )
    return analyses
