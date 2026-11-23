"""Output of the backfill-recovery experiment: CSV files, a manifest and a Markdown report."""

from __future__ import annotations

import json
import platform
from dataclasses import asdict, fields
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from vintage_lakehouse.experiment.recovery import (
    RecoveryAnalysis,
    RecoveryEstimate,
    RecoveryResult,
    VariantComparison,
)
from vintage_lakehouse.experiment.report import _PACKAGES, _jsonable, _pct, _table, _write_csv
from vintage_lakehouse.experiment.runner import Scenario


def build_recovery_manifest(
    result: RecoveryResult,
    *,
    data_id: str | None,
    argv: list[str],
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    """Describe a run: configuration, data identifier, environment and timings."""
    versions: dict[str, str] = {}
    for name in _PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "command": argv,
        "dataset_id": data_id,
        "config": _jsonable(result.config),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": versions,
        "seconds": result.seconds,
        "scored_base_forecasts": len(result.base_rows),
        "scored_corrected_forecasts": len(result.corrected_rows),
        "skipped_origins": len(result.skipped),
    }


def _interval(value: float | None, low: float | None, high: float | None, fmt: str) -> str:
    if value is None or low is None or high is None:
        return "n/a" if value is None else fmt.format(value)
    return f"{fmt.format(value)} [{fmt.format(low)}, {fmt.format(high)}]"


def _pct_interval(value: float | None, low: float | None, high: float | None) -> str:
    if value is None:
        return "n/a"
    if low is None or high is None:
        return _pct(value)
    return f"{_pct(value)} [{_pct(low)}, {_pct(high)}]"


def _improvement_cell(e: RecoveryEstimate) -> str:
    return _interval(e.improvement, e.improvement_low, e.improvement_high, "{:+.4f}")


def _recovery_cell(e: RecoveryEstimate) -> str:
    if e.recovery is None:
        return "not computed"
    return _pct_interval(e.recovery, e.recovery_low, e.recovery_high)


def render_recovery_report(
    result: RecoveryResult,
    analyses: dict[Scenario, RecoveryAnalysis],
    *,
    data_id: str | None,
    generated_at: datetime,
) -> str:
    """Write the results as Markdown."""
    config = result.config
    experiment = config.experiment
    correction = config.correction
    ci = f"{experiment.confidence:.0%}"
    origins = experiment.origins()
    parts: list[str] = [
        "# Backfill-recovery experiment",
        "",
        f"Generated {generated_at:%Y-%m-%d %H:%M} UTC. Data identifier: `{data_id or 'unknown'}`.",
        "",
        "## Setup",
        "",
        f"- Series: `{experiment.series_id}`; models: {', '.join(experiment.models)}",
        f"- Forecast origins: {len(origins)}, every {experiment.origin_step_weeks} weeks from "
        f"{experiment.first_origin:%Y-%m-%d} to {experiment.last_origin:%Y-%m-%d}; horizons "
        + ", ".join(map(str, experiment.horizons)),
        f"- Hindsight and truth taken at T_eval {experiment.evaluation_time:%Y-%m-%d}",
        f"- Correction: median revision of the last {correction.max_lag} reported weeks, "
        f"estimated from weeks that were at least {correction.settle} weeks old (at least "
        f"{correction.min_samples} pairs per lag), using only what was known at each origin",
        f"- Variants: {', '.join(config.variants)} (`backfill-placebo` applies the factors to "
        "the wrong lags, as a control)",
        f"- {ci} percentile intervals from {experiment.n_boot} moving-block bootstrap resamples "
        f"of the origins (blocks of {experiment.block_length()} origins), seed {experiment.seed}",
        "",
        "For matched forecasts (same model, origin and horizon), `improvement` is the mean WIS "
        "saved by the correction in real time (positive is better). `recovery` is the improvement "
        "divided by what hindsight data would have saved, i.e. the share of the leakage gap that "
        "the correction recovers. It is computed only for models whose leakage gap interval "
        "excludes zero.",
        "",
    ]

    for index, (scenario, analysis) in enumerate(analyses.items()):
        heading = "Headline" if index == 0 else "Sensitivity"
        parts += [f"## {heading}: {scenario.label}", ""]
        if not analysis.estimates:
            parts += ["No forecasts were scored.", ""]
            continue
        gap_rows = [
            [
                m,
                f"{g.wis_realtime:.4f}",
                f"{g.wis_hindsight:.4f}",
                _pct_interval(g.gap, g.gap_low, g.gap_high),
            ]
            for m, g in analysis.gaps.items()
        ]
        parts += [
            "Leakage gap of the base models on the same origins:",
            "",
            _table(["Model", "WIS real time", "WIS hindsight", f"Gap [{ci} CI]"], gap_rows),
            "",
        ]
        pooled = [e for e in analysis.estimates if e.horizon is None]
        parts += [
            _table(
                [
                    "Model",
                    "Variant",
                    "Pairs",
                    "WIS corrected",
                    f"Improvement [{ci} CI]",
                    f"Relative [{ci} CI]",
                    f"Recovery [{ci} CI]",
                ],
                [
                    [
                        e.model,
                        e.variant,
                        str(e.n_pairs),
                        f"{e.wis_corrected:.4f}",
                        _improvement_cell(e),
                        _pct_interval(e.relative_improvement, e.relative_low, e.relative_high),
                        _recovery_cell(e),
                    ]
                    for e in pooled
                ],
            ),
            "",
        ]
        if index == 0 and analysis.comparisons:
            parts += [
                "Difference in mean improvement between variants (variant minus versus):",
                "",
                _table(
                    ["Model", "Variant", "Versus", f"Difference [{ci} CI]"],
                    [
                        [
                            c.model,
                            c.variant,
                            c.versus,
                            _interval(c.difference, c.low, c.high, "{:+.4f}"),
                        ]
                        for c in analysis.comparisons
                    ],
                ),
                "",
            ]
        if index == 0:
            primary = config.variants[0]
            parts += [
                f"By horizon, variant `{primary}`:",
                "",
                _table(
                    ["Model", "Horizon", "Pairs", f"Improvement [{ci} CI]", "Recovery"],
                    [
                        [
                            e.model,
                            str(e.horizon),
                            str(e.n_pairs),
                            _improvement_cell(e),
                            _pct(e.recovery) if e.recovery is not None else "not computed",
                        ]
                        for e in analysis.estimates
                        if e.variant == primary and e.horizon is not None
                    ],
                ),
                "",
            ]

    if result.factors_by_origin:
        lag0 = [f.adjustment[0] for f in result.factors_by_origin.values()]
        last_origin = max(result.factors_by_origin)
        last = result.factors_by_origin[last_origin]
        parts += [
            f"## What the `{config.variants[0]}` correction does",
            "",
            f"Estimated at the last origin ({last_origin:%Y-%m-%d}), per lag (0 is the latest "
            f"reported week):",
            "",
            _table(
                ["Lag", "Adjustment", "Pairs"],
                [
                    [str(lag), f"{value:.4f}", str(last.samples[lag])]
                    for lag, value in last.adjustment.items()
                ],
            ),
            "",
            f"The lag-0 adjustment ranged from {min(lag0):.4f} to {max(lag0):.4f} across origins "
            f"(`factors.csv` has all of them).",
            "",
        ]

    parts += [
        "## Reading these numbers",
        "",
        "- They describe a simple correction (median revision by age) wrapped around three simple, "
        "untuned baselines on one series. They say nothing about other series, models or "
        "revision patterns.",
        "- The correction only changes the values the model sees. The model's prediction "
        "intervals are not widened for the uncertainty of the correction.",
        "- Intervals express uncertainty about which origins were sampled, with a block bootstrap. "
        "They do not cover the choice of correction settings (fixed in advance, not tuned) or of "
        "T_eval.",
        "- Recovery is not computed when a model's own leakage gap interval includes zero. "
        "Improvement is always given, in WIS units and relative.",
        "- Exploratory: no correction for multiple comparisons. The plan was fixed before the run "
        "(docs/adr/0010-backfill-correction.md).",
        "",
    ]
    return "\n".join(parts)


def write_recovery_outputs(
    out_dir: Path,
    result: RecoveryResult,
    analyses: dict[Scenario, RecoveryAnalysis],
    manifest: dict[str, Any],
    report: str,
) -> list[Path]:
    """Write the report, manifest and CSV files into ``out_dir`` and return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    def target(name: str) -> Path:
        path = out_dir / name
        paths.append(path)
        return path

    estimate_fields = [f.name for f in fields(RecoveryEstimate)]
    _write_csv(
        target("recovery.csv"),
        ["scenario", "n_origins", *estimate_fields],
        [
            [scenario.label, analysis.n_origins, *(asdict(e)[name] for name in estimate_fields)]
            for scenario, analysis in analyses.items()
            for e in analysis.estimates
        ],
    )
    comparison_fields = [f.name for f in fields(VariantComparison)]
    _write_csv(
        target("variant_comparisons.csv"),
        ["scenario", *comparison_fields],
        [
            [scenario.label, *(asdict(c)[name] for name in comparison_fields)]
            for scenario, analysis in analyses.items()
            for c in analysis.comparisons
        ],
    )
    _write_csv(
        target("leakage_gap.csv"),
        ["scenario", "model", "n_pairs", "wis_realtime", "wis_hindsight", "gap", "low", "high"],
        [
            [
                scenario.label,
                m,
                g.n_pairs,
                g.wis_realtime,
                g.wis_hindsight,
                g.gap,
                g.gap_low,
                g.gap_high,
            ]
            for scenario, analysis in analyses.items()
            for m, g in analysis.gaps.items()
        ],
    )
    _write_csv(
        target("factors.csv"),
        ["origin", "kind", "lag", "adjustment", "samples"],
        [
            [origin, factors.kind, lag, value, factors.samples[lag]]
            for origin, factors in sorted(result.factors_by_origin.items())
            for lag, value in factors.adjustment.items()
        ],
    )
    _write_csv(
        target("scores.csv"),
        ["model", "origin", "horizon", "target", "mode", "truth", "median", "wis", "abs_error"],
        [
            [
                r.model,
                r.origin,
                r.horizon,
                r.target,
                r.mode.value,
                r.truth,
                r.median,
                r.wis,
                r.abs_error,
            ]
            for r in [*result.base_rows, *result.corrected_rows]
        ],
    )
    target("manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    target("report.md").write_text(report, encoding="utf-8", newline="\n")
    return paths
