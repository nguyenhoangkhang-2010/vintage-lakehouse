"""Command-line entry point for vintage-lakehouse."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from vintage_lakehouse import __version__
from vintage_lakehouse.config import Settings
from vintage_lakehouse.experiment import command as experiment_command
from vintage_lakehouse.experiment import recovery_command
from vintage_lakehouse.ingestion import delphi
from vintage_lakehouse.ingestion.archive import RawArchive
from vintage_lakehouse.ingestion.epiweek import date_to_epiweek
from vintage_lakehouse.ingestion.errors import FetchError
from vintage_lakehouse.ingestion.gate import format_markdown
from vintage_lakehouse.ingestion.lineage import IngestReport, LineageLog
from vintage_lakehouse.ingestion.pipeline import IngestFailed, archived_chunks, run_ingestion

_REGION = re.compile(r"[A-Za-z0-9_-]+")


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="vintage-lakehouse",
        description="Point-in-time lakehouse for revised data and hindsight-bias replay.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("info", help="Print the resolved configuration.")

    gate = subparsers.add_parser(
        "gate", help="Probe a data source against the week-0 gate criteria (needs network)."
    )
    gate.add_argument("source", choices=[delphi.SOURCE_ID], help="Source to probe.")

    ingest = subparsers.add_parser(
        "ingest",
        help="Load a source into the local store (needs network unless --from-archive).",
    )
    ingest.add_argument("source", choices=[delphi.SOURCE_ID], help="Source to load.")
    ingest.add_argument("--region", default="nat", help="FluView region (default: nat).")
    ingest.add_argument("--first-year", type=int, default=2010, help="First epiweek year.")
    ingest.add_argument("--last-year", type=int, help="Last epiweek year (default: this year).")
    ingest.add_argument(
        "--last-issue",
        type=int,
        help="Latest publication epiweek to include, for example 202638 (default: this week). "
        "Fixing it makes a pull reproducible.",
    )
    ingest.add_argument(
        "--data-dir", type=Path, help="Where the warehouse, raw archive and lineage log live."
    )
    ingest.add_argument(
        "--from-archive",
        action="store_true",
        help="Replay the saved raw responses instead of using the network.",
    )

    experiment = subparsers.add_parser("experiment", help="Run an experiment on the stored data.")
    experiments = experiment.add_subparsers(dest="experiment", required=True)
    leakage_gap = experiments.add_parser(
        "leakage-gap",
        help="Measure how much hindsight evaluation overstates real-time forecast skill.",
    )
    experiment_command.add_arguments(leakage_gap)
    backfill_recovery = experiments.add_parser(
        "backfill-recovery",
        help="Measure how much of the leakage gap a backfill correction recovers in real time.",
    )
    recovery_command.add_arguments(backfill_recovery)
    return parser


def _run_gate() -> int:
    try:
        report = delphi.run_gate()
    except FetchError as exc:
        print(f"gate could not run: {exc}", file=sys.stderr)
        return 2
    print(format_markdown(report))
    if report.manual:
        numbers = ", ".join(str(c.number) for c in report.manual)
        print(f"\nCriteria still to check by a human: {numbers}. Record them in DATA_SOURCES.md.")
    return 0 if report.passed else 1


def _label(request: str) -> str:
    match = re.search(r"epiweeks=([^&]+)", request)
    return match.group(1) if match else request


def _print_reports(reports: Sequence[IngestReport]) -> None:
    for r in reports:
        print(
            f"{_label(r.request)}: {r.rows_fetched} rows -> {r.new_versions} new versions "
            f"({r.new_periods} new periods, {r.revisions} revisions, {r.unchanged} unchanged); "
            f"{r.duplicates} already stored"
        )
    largest = max(
        (r.max_abs_revision for r in reports if r.max_abs_revision is not None), default=None
    )
    print(
        f"total: {sum(r.rows_fetched for r in reports)} rows, "
        f"{sum(r.new_versions for r in reports)} new versions, "
        f"{sum(r.revisions for r in reports)} revisions"
        + (f", largest revision {largest:.4g}" if largest is not None else "")
    )


def _run_ingest(args: argparse.Namespace) -> int:
    try:
        from vintage_lakehouse.storage.iceberg import IcebergStore
    except ImportError:
        print(
            'the Iceberg store is not installed; run: pip install -e ".[iceberg]"', file=sys.stderr
        )
        return 2

    if not _REGION.fullmatch(args.region):
        print(f"invalid region: {args.region!r}", file=sys.stderr)
        return 2
    today = date.today()
    last_year = args.last_year or today.year
    last_issue = args.last_issue or date_to_epiweek(today)
    if args.first_year > last_year:
        print("--first-year must not be after --last-year", file=sys.stderr)
        return 2

    data_dir = (args.data_dir or Settings.from_env().data_dir).resolve()
    source = delphi.FluViewSource(region=args.region)
    archive = RawArchive(data_dir / "raw", f"{delphi.SOURCE_ID}/{args.region}")
    lineage = LineageLog(data_dir / "lineage" / "ingestion.jsonl")
    store = IcebergStore.local(data_dir / "warehouse")
    try:
        if args.from_archive:
            reports = run_ingestion(
                store,
                archived_chunks(archive, source.parse),
                source=delphi.SOURCE_ID,
                lineage=lineage,
            )
        else:
            reports = run_ingestion(
                store,
                source.chunks(args.first_year, last_year, last_issue),
                source=delphi.SOURCE_ID,
                lineage=lineage,
                archive=archive,
            )
    except FetchError as exc:
        print(f"ingestion could not run: {exc}", file=sys.stderr)
        return 2
    except IngestFailed as exc:
        _print_reports(exc.reports[:-1])
        print(f"ingestion failed: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    _print_reports(reports)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "info":
        settings = Settings.from_env()
        print(f"vintage-lakehouse {__version__}")
        print(f"data_dir: {settings.data_dir}")
        return 0
    if args.command == "gate":
        return _run_gate()
    if args.command == "ingest":
        return _run_ingest(args)
    if args.command == "experiment":
        if args.experiment == "backfill-recovery":
            return recovery_command.run(args)
        return experiment_command.run(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
