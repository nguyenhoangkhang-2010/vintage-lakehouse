"""Command-line entry point for vintage-lakehouse."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from vintage_lakehouse import __version__
from vintage_lakehouse.config import Settings


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="vintage-lakehouse",
        description="Point-in-time lakehouse for revised data and hindsight-bias replay.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("info", help="Print the resolved configuration.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "info":
        settings = Settings.from_env()
        print(f"vintage-lakehouse {__version__}")
        print(f"data_dir: {settings.data_dir}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
