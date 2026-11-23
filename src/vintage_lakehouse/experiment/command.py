"""The ``vintage-lakehouse experiment leakage-gap`` command."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from vintage_lakehouse.config import Settings
from vintage_lakehouse.experiment.report import (
    build_manifest,
    dataset_id,
    render_report,
    write_outputs,
)
from vintage_lakehouse.experiment.runner import (
    MODEL_NAMES,
    ExperimentConfig,
    make_forecasters,
    run_analyses,
    run_experiment,
)
from vintage_lakehouse.ingestion.lineage import LineageLog

UTC = timezone.utc
_DEFAULTS = ExperimentConfig()


def _date(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {text!r}") from exc


def _ints(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in text.split(",") if part)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"not a comma-separated list of integers: {text!r}"
        ) from exc


def _names(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the options of ``experiment leakage-gap``."""
    parser.add_argument("--data-dir", type=Path, help="Where the warehouse and lineage live.")
    parser.add_argument("--out", type=Path, default=Path("results"), help="Output directory.")
    parser.add_argument("--series", default=_DEFAULTS.series_id, help="Series to replay.")
    parser.add_argument(
        "--models",
        type=_names,
        default=_DEFAULTS.models,
        help=f"Comma-separated models from {', '.join(MODEL_NAMES)} (default: all).",
    )
    parser.add_argument("--first-origin", type=_date, default=_DEFAULTS.first_origin)
    parser.add_argument("--last-origin", type=_date, default=_DEFAULTS.last_origin)
    parser.add_argument("--origin-step-weeks", type=int, default=_DEFAULTS.origin_step_weeks)
    parser.add_argument("--horizons", type=_ints, default=_DEFAULTS.horizons)
    parser.add_argument(
        "--evaluation-time",
        type=_date,
        default=_DEFAULTS.evaluation_time,
        help="Primary T_eval: when the hindsight data is taken (default: end of the data).",
    )
    parser.add_argument("--n-boot", type=int, default=_DEFAULTS.n_boot)
    parser.add_argument("--block-weeks", type=int, default=_DEFAULTS.block_weeks)
    parser.add_argument("--confidence", type=float, default=_DEFAULTS.confidence)
    parser.add_argument("--seed", type=int, default=_DEFAULTS.seed)


def run(args: argparse.Namespace) -> int:
    """Run the experiment, print a summary and write the result files."""
    try:
        from vintage_lakehouse.storage.iceberg import IcebergStore
    except ImportError:
        print(
            'the Iceberg store is not installed; run: pip install -e ".[iceberg]"', file=sys.stderr
        )
        return 2

    try:
        config = ExperimentConfig(
            series_id=args.series,
            models=args.models,
            first_origin=args.first_origin,
            last_origin=args.last_origin,
            origin_step_weeks=args.origin_step_weeks,
            horizons=args.horizons,
            evaluation_time=args.evaluation_time,
            n_boot=args.n_boot,
            block_weeks=args.block_weeks,
            confidence=args.confidence,
            seed=args.seed,
        )
        forecasters = make_forecasters(config.models, config.lower_bound)
    except ValueError as exc:
        print(f"invalid experiment settings: {exc}", file=sys.stderr)
        return 2
    except ImportError:
        print(
            "ETS and gradient boosting need the baselines extra; "
            'run: pip install -e ".[baselines]"',
            file=sys.stderr,
        )
        return 2

    data_dir = (args.data_dir or Settings.from_env().data_dir).resolve()
    store = IcebergStore.local(data_dir / "warehouse")
    started_at = datetime.now(UTC)
    try:
        result = run_experiment(store, forecasters, config, progress=print)
    finally:
        store.close()

    if not any(result.rows_by_eval.values()):
        print(
            "no forecasts were scored: check the series, the origins and that data was ingested",
            file=sys.stderr,
        )
        return 2

    print(f"analysing ({config.n_boot} bootstrap resamples per scenario) ...")
    analyses = run_analyses(result)
    finished_at = datetime.now(UTC)
    data_id = dataset_id(LineageLog(data_dir / "lineage" / "ingestion.jsonl"))
    manifest = build_manifest(
        result,
        data_id=data_id,
        argv=sys.argv[1:],
        started_at=started_at,
        finished_at=finished_at,
    )
    report = render_report(result, analyses, data_id=data_id, generated_at=finished_at)
    paths = write_outputs(args.out, result, analyses, manifest, report)

    print()
    print(report)
    print("files written:")
    for path in paths:
        print(f"  {path}")
    return 0
