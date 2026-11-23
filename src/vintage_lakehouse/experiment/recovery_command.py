"""The ``vintage-lakehouse experiment backfill-recovery`` command."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from vintage_lakehouse.backfill import CorrectionConfig
from vintage_lakehouse.config import Settings
from vintage_lakehouse.experiment import command as leakage_command
from vintage_lakehouse.experiment.recovery import (
    VARIANTS,
    RecoveryConfig,
    run_recovery,
    run_recovery_analyses,
)
from vintage_lakehouse.experiment.recovery_report import (
    build_recovery_manifest,
    render_recovery_report,
    write_recovery_outputs,
)
from vintage_lakehouse.experiment.report import dataset_id
from vintage_lakehouse.experiment.runner import ExperimentConfig, make_forecasters
from vintage_lakehouse.ingestion.lineage import LineageLog

UTC = timezone.utc
_CORRECTION = CorrectionConfig()


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the options of ``experiment backfill-recovery``."""
    leakage_command.add_arguments(parser)
    parser.set_defaults(out=Path("results") / "backfill-recovery")
    parser.add_argument(
        "--variants",
        type=leakage_command._names,
        default=VARIANTS,
        help=f"Comma-separated variants from {', '.join(VARIANTS)}; the first is the primary one.",
    )
    parser.add_argument("--max-lag", type=int, default=_CORRECTION.max_lag)
    parser.add_argument("--settle", type=int, default=_CORRECTION.settle)
    parser.add_argument("--min-samples", type=int, default=_CORRECTION.min_samples)


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
        experiment = ExperimentConfig(
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
        config = RecoveryConfig(
            experiment=experiment,
            variants=args.variants,
            correction=CorrectionConfig(
                max_lag=args.max_lag,
                settle=args.settle,
                min_samples=args.min_samples,
                step_days=experiment.step_days,
            ),
        )
        forecasters = make_forecasters(experiment.models, experiment.lower_bound)
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
        result = run_recovery(store, config, forecasters=forecasters, progress=print)
    finally:
        store.close()

    if not result.base_rows:
        print(
            "no forecasts were scored: check the series, the origins and that data was ingested",
            file=sys.stderr,
        )
        return 2

    print(f"analysing ({experiment.n_boot} bootstrap resamples per scenario) ...")
    analyses = run_recovery_analyses(result)
    finished_at = datetime.now(UTC)
    data_id = dataset_id(LineageLog(data_dir / "lineage" / "ingestion.jsonl"))
    manifest = build_recovery_manifest(
        result, data_id=data_id, argv=sys.argv[1:], started_at=started_at, finished_at=finished_at
    )
    report = render_recovery_report(result, analyses, data_id=data_id, generated_at=finished_at)
    paths = write_recovery_outputs(args.out, result, analyses, manifest, report)

    print()
    print(report)
    print("files written:")
    for path in paths:
        print(f"  {path}")
    return 0
