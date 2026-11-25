"""The ``vintage-lakehouse benchmark`` command."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from vintage_lakehouse.benchmarks.report import render_markdown, results_to_dict, write_outputs
from vintage_lakehouse.benchmarks.suite import STORES, BenchConfig, measure_real, run_benchmarks
from vintage_lakehouse.config import Settings

DEFAULT_SERIES = "delphi/fluview/nat/wili"
_DEFAULTS = BenchConfig()


def _names(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the options of ``benchmark``."""
    parser.add_argument(
        "--quick", action="store_true", help="Tiny sizes: finishes in seconds, numbers are rough."
    )
    parser.add_argument(
        "--stores",
        type=_names,
        default=STORES,
        help=f"Comma-separated stores from {', '.join(STORES)} (default: all).",
    )
    parser.add_argument("--out", type=Path, default=Path("results") / "benchmarks")
    parser.add_argument("--seed", type=int, default=_DEFAULTS.seed)
    parser.add_argument("--queries", type=int, help="Timed queries per point.")
    parser.add_argument(
        "--from-store",
        action="store_true",
        help="Also measure the stored (real) series: latency and storage overhead.",
    )
    parser.add_argument("--series", default=DEFAULT_SERIES, help="Series for --from-store.")
    parser.add_argument("--data-dir", type=Path, help="Where the warehouse lives (--from-store).")


def run(args: argparse.Namespace) -> int:
    """Run the benchmarks, print the report and write the result files."""
    overrides: dict[str, object] = {"stores": args.stores, "seed": args.seed}
    if args.queries is not None:
        overrides["queries"] = args.queries
    try:
        config = (
            BenchConfig.quick(**overrides) if args.quick else BenchConfig(**overrides)  # type: ignore[arg-type]
        )
    except ValueError as exc:
        print(f"invalid settings: {exc}", file=sys.stderr)
        return 2
    if "iceberg" in config.stores or args.from_store:
        try:
            import vintage_lakehouse.storage.iceberg  # noqa: F401
        except ImportError:
            print(
                'the Iceberg store is not installed; run: pip install -e ".[iceberg]"',
                file=sys.stderr,
            )
            return 2

    results = run_benchmarks(config, progress=print)
    if args.from_store:
        from vintage_lakehouse.storage.iceberg import IcebergStore

        data_dir = (args.data_dir or Settings.from_env().data_dir).resolve()
        warehouse = data_dir / "warehouse"
        print(f"measuring the stored series {args.series!r} (loading its versions first) ...")
        store = IcebergStore.local(warehouse)
        try:
            points, storage = measure_real(
                store, args.series, "iceberg", config, warehouse=warehouse
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        finally:
            store.close()
        results = replace(
            results,
            latency=[*results.latency, *points],
            storage=[*results.storage, storage],
            notes=[
                *results.notes,
                f"The real-data rows are the stored series {args.series!r}; the Iceberg size is "
                "that of the whole warehouse folder.",
            ],
        )

    generated_at = datetime.now(timezone.utc)
    report = render_markdown(results, generated_at=generated_at)
    paths = write_outputs(args.out, report, json.dumps(results_to_dict(results), indent=2) + "\n")
    print()
    print(report)
    print("files written:")
    for path in paths:
        print(f"  {path}")
    return 0
