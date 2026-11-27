"""Output of the leakage-gap experiment: CSV files, a manifest, and a Markdown report.

Everything a reader needs to check or reproduce a run is written next to the numbers: the exact
configuration, an identifier of the data (a hash of the raw responses it was built from), and the
versions of the libraries used.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from vintage_lakehouse.experiment.analysis import GapEstimate
from vintage_lakehouse.experiment.runner import ExperimentAnalyses, ExperimentResult
from vintage_lakehouse.ingestion.lineage import LineageLog

_PACKAGES = (
    "vintage-lakehouse",
    "numpy",
    "pandas",
    "scikit-learn",
    "statsmodels",
    "pyiceberg",
    "duckdb",
    "pyarrow",
)


def dataset_id(lineage: LineageLog) -> str | None:
    """A hash of the raw responses that the store was built from (None if nothing was logged)."""
    hashes = sorted({r.raw_sha256 for r in lineage.read() if r.raw_sha256 and r.status == "ok"})
    if not hashes:
        return None
    return hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def build_manifest(
    result: ExperimentResult,
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
        "seconds_by_evaluation_time": _jsonable(result.seconds_by_eval),
        "forecasts_requested": result.cache_requested,
        "forecasts_computed": result.cache_computed,
        "scored_forecasts": {t.isoformat(): len(rows) for t, rows in result.rows_by_eval.items()},
        "skipped_origins": {t.isoformat(): len(s) for t, s in result.skipped_by_eval.items()},
    }


# --- Markdown -----------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _gap_cell(e: GapEstimate) -> str:
    if e.gap is None:
        return "n/a"
    return f"{_pct(e.gap)} [{_pct(e.gap_low)}, {_pct(e.gap_high)}]"


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def render_report(
    result: ExperimentResult,
    analyses: ExperimentAnalyses,
    *,
    data_id: str | None,
    generated_at: datetime,
) -> str:
    """Write the results as Markdown."""
    config = result.config
    ci = f"{config.confidence:.0%}"
    origins = config.origins()
    parts: list[str] = [
        "# Leakage-gap experiment",
        "",
        f"Generated {generated_at:%Y-%m-%d %H:%M} UTC. Data identifier: `{data_id or 'unknown'}`.",
        "",
        "## Setup",
        "",
        f"- Series: `{config.series_id}`",
        f"- Models: {', '.join(config.models)}",
        f"- Forecast origins: {len(origins)}, every {config.origin_step_weeks} weeks from "
        f"{config.first_origin:%Y-%m-%d} to {config.last_origin:%Y-%m-%d}",
        "- Horizons (weeks ahead of the latest reported week): "
        + ", ".join(map(str, config.horizons)),
        f"- Primary evaluation time T_eval: {config.evaluation_time:%Y-%m-%d}; "
        f"sensitivity T_eval: "
        f"{', '.join(f'{t:%Y-%m-%d}' for t in config.evaluation_times()[:-1]) or 'none'}",
        f"- Confidence intervals: {ci} percentile intervals from {config.n_boot} moving-block "
        f"bootstrap resamples of the origins (blocks of {config.block_length()} origins, "
        f"about {config.block_weeks} weeks), seed {config.seed}",
        "",
        "The gap is `(WIS real time - WIS hindsight) / WIS real time`: the relative improvement a "
        "model shows when it is given revised (hindsight) data instead of what was known at the "
        "time. Pooled figures are ratios of mean WIS over all horizons.",
        "",
    ]

    primary = analyses.by_scenario[config.primary]
    parts += [f"## Headline: {config.primary.label}", ""]
    rows = []
    for e in primary.estimates:
        rows.append(
            [
                e.model,
                "all" if e.horizon is None else str(e.horizon),
                str(e.n_pairs),
                f"{e.wis_realtime:.4f}",
                f"{e.wis_hindsight:.4f}",
                _gap_cell(e),
            ]
        )
    parts += [
        _table(
            ["Model", "Horizon", "Pairs", "WIS real time", "WIS hindsight", f"Gap [{ci} CI]"], rows
        ),
        "",
    ]

    if primary.comparisons:
        parts += ["## Do the models differ?", "", "Difference between pooled gaps (A minus B).", ""]
        parts += [
            _table(
                ["A", "B", f"Difference [{ci} CI]"],
                [
                    [
                        c.model_a,
                        c.model_b,
                        f"{_pct(c.difference)} [{_pct(c.low)}, {_pct(c.high)}]",
                    ]
                    for c in primary.comparisons
                ],
            ),
            "",
        ]

    parts += ["## Sensitivity", "", "Pooled gap per model, by evaluation time and origin set.", ""]
    models = list(config.models)
    sensitivity_rows = []
    for scenario, analysis in analyses.by_scenario.items():
        pooled = {e.model: e for e in analysis.estimates if e.horizon is None}
        sensitivity_rows.append(
            [scenario.label, str(analysis.n_origins)]
            + [_gap_cell(pooled[m]) if m in pooled else "n/a" for m in models]
        )
    parts += [_table(["Scenario", "Origins", *models], sensitivity_rows), ""]

    if analyses.revision_correlations:
        parts += [
            "## Does the gap grow with the size of recent revisions?",
            "",
            "For each origin, the size of the revision is how much the last "
            f"{config.revision_window} reported weeks were later revised, on average. Origins are "
            "split into three equal groups of increasing revision size.",
            "",
        ]
        parts += [
            _table(
                ["Model", "Group", "Origins", "Revision size", "Gap"],
                [
                    [
                        g.model,
                        str(g.group),
                        str(g.n_origins),
                        f"{g.revision_low:.3f} to {g.revision_high:.3f}",
                        _pct(g.gap),
                    ]
                    for g in analyses.revision_groups
                ],
            ),
            "",
            "Spearman correlation between revision size and the per-origin WIS degradation:",
            "",
            _table(
                ["Model", "Origins", f"Spearman [{ci} CI]"],
                [
                    [
                        c.model,
                        str(c.n_origins),
                        "n/a"
                        if c.spearman is None
                        else f"{c.spearman:.2f} [{c.low:.2f}, {c.high:.2f}]",
                    ]
                    for c in analyses.revision_correlations
                ],
            ),
            "",
        ]

    parts += [
        "## Reading these numbers",
        "",
        "- They describe three simple, untuned baselines on one series (US national weighted ILI "
        "from FluView), not forecasting methods in general and not any other disease or country.",
        "- The intervals express uncertainty about which origins were sampled, using a block "
        "bootstrap to respect the dependence between nearby origins. They do not cover model "
        "misspecification or the choice of T_eval; the sensitivity table shows the latter.",
        "- A relative gap is unstable when the real-time WIS is small. Compare it with the "
        "absolute degradation in `leakage_gap.csv`.",
        "- No correction for multiple comparisons was applied. The analysis plan was fixed before "
        "the run (docs/adr/0009-leakage-gap-experiment.md).",
        "",
    ]
    return "\n".join(parts)


# --- CSV and files ------------------------------------------------------------------------


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([_cell(value) for value in row])


def write_outputs(
    out_dir: Path,
    result: ExperimentResult,
    analyses: ExperimentAnalyses,
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

    estimate_fields = [f.name for f in fields(GapEstimate)]
    _write_csv(
        target("leakage_gap.csv"),
        ["scenario", "evaluation_time", "excluded_origins", "n_origins", *estimate_fields],
        [
            [
                scenario.label,
                scenario.evaluation_time,
                "; ".join(f"{a:%Y-%m-%d} to {b:%Y-%m-%d}" for a, b in scenario.excluded),
                analysis.n_origins,
                *(asdict(e)[name] for name in estimate_fields),
            ]
            for scenario, analysis in analyses.by_scenario.items()
            for e in analysis.estimates
        ],
    )
    _write_csv(
        target("model_comparisons.csv"),
        ["scenario", "model_a", "model_b", "difference", "low", "high", "n_valid"],
        [
            [scenario.label, c.model_a, c.model_b, c.difference, c.low, c.high, c.n_valid]
            for scenario, analysis in analyses.by_scenario.items()
            for c in analysis.comparisons
        ],
    )
    _write_csv(
        target("revision_size_groups.csv"),
        ["model", "group", "n_origins", "revision_low", "revision_high", "gap"],
        [
            [g.model, g.group, g.n_origins, g.revision_low, g.revision_high, g.gap]
            for g in analyses.revision_groups
        ],
    )
    _write_csv(
        target("revision_size_correlation.csv"),
        ["model", "n_origins", "spearman", "low", "high"],
        [[c.model, c.n_origins, c.spearman, c.low, c.high] for c in analyses.revision_correlations],
    )
    _write_csv(
        target("scores.csv"),
        [
            "evaluation_time",
            "model",
            "origin",
            "horizon",
            "target",
            "mode",
            "truth",
            "median",
            "wis",
            "abs_error",
        ],
        [
            [
                evaluation_time,
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
            for evaluation_time, rows in result.rows_by_eval.items()
            for r in rows
        ],
    )
    target("manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    target("report.md").write_text(report, encoding="utf-8", newline="\n")
    return paths
