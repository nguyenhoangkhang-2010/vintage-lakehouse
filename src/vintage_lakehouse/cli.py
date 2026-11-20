"""Command-line entry point for vintage-lakehouse."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from vintage_lakehouse import __version__
from vintage_lakehouse.config import Settings
from vintage_lakehouse.ingestion import delphi
from vintage_lakehouse.ingestion.errors import FetchError
from vintage_lakehouse.ingestion.gate import format_markdown


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
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
