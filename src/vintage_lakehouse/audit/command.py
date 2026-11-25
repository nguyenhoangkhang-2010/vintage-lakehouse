"""The ``vintage-lakehouse audit`` commands: ``trust-report``, ``lint`` and ``leak-check``."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vintage_lakehouse.audit.leak_check import Pipeline, check_pipeline
from vintage_lakehouse.audit.lint import RULES, lint_paths
from vintage_lakehouse.audit.trust import (
    TrustConfig,
    build_trust_report,
    render_trust_markdown,
    trust_report_to_dict,
)
from vintage_lakehouse.config import Settings

UTC = timezone.utc
DEFAULT_SERIES = "delphi/fluview/nat/wili"


def _moment(text: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date or time: {text!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def add_arguments(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the three audit commands."""
    trust = subparsers.add_parser(
        "trust-report", help="Describe how the values of a series get revised."
    )
    trust.add_argument("--series", default=DEFAULT_SERIES)
    trust.add_argument("--data-dir", type=Path)
    trust.add_argument("--as-of", type=_moment, help="Use only what was known by this time.")
    trust.add_argument("--format", choices=("markdown", "json"), default="markdown")
    trust.add_argument("--out", type=Path, help="Write to this file instead of the screen.")
    trust.add_argument("--settle", type=int, default=TrustConfig().settle)

    lint = subparsers.add_parser(
        "lint", help="Flag code patterns that often leak the future (a heuristic aid)."
    )
    lint.add_argument("paths", nargs="+", type=Path, help="Python files or folders.")

    leak = subparsers.add_parser(
        "leak-check", help="Run a pipeline with later data altered and removed to find look-ahead."
    )
    leak.add_argument("--pipeline", required=True, help="module:function to call.")
    leak.add_argument(
        "--pipeline-path", type=Path, action="append", default=[], help="Folder to import from."
    )
    leak.add_argument("--series", default=DEFAULT_SERIES)
    leak.add_argument("--data-dir", type=Path)
    leak.add_argument("--first-origin", type=_moment, default=datetime(2015, 1, 3, tzinfo=UTC))
    leak.add_argument("--last-origin", type=_moment, default=datetime(2024, 12, 28, tzinfo=UTC))
    leak.add_argument("--step-weeks", type=int, default=52)


def run(args: argparse.Namespace) -> int:
    """Run the chosen audit command and return the exit code."""
    if args.audit == "lint":
        return _lint(args)
    if args.audit == "trust-report":
        return _trust(args)
    return _leak_check(args)


def _lint(args: argparse.Namespace) -> int:
    try:
        findings = lint_paths(args.paths)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for f in findings:
        print(f"{f.filename}:{f.line}:{f.column + 1}: {f.rule} {f.message}")
    if findings:
        rules = sorted({f.rule for f in findings})
        print(f"{len(findings)} finding(s): " + ", ".join(f"{r} ({RULES[r]})" for r in rules))
        print("This is a heuristic: review each one. `audit leak-check` tests behaviour.")
        return 1
    print("No findings. This does not prove the code is free of look-ahead.")
    return 0


def _open_store(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    try:
        from vintage_lakehouse.storage.iceberg import IcebergStore
    except ImportError:
        print(
            'the Iceberg store is not installed; run: pip install -e ".[iceberg]"', file=sys.stderr
        )
        return None
    data_dir = (args.data_dir or Settings.from_env().data_dir).resolve()
    return IcebergStore.local(data_dir / "warehouse")


def _trust(args: argparse.Namespace) -> int:
    from vintage_lakehouse.backfill import VintageTable

    try:
        config = TrustConfig(settle=args.settle)
    except ValueError as exc:
        print(f"invalid settings: {exc}", file=sys.stderr)
        return 2
    store = _open_store(args)
    if store is None:
        return 2
    try:
        limit = args.as_of or datetime(2200, 1, 1, tzinfo=UTC)
        table = VintageTable.from_store(store, args.series, limit)
    finally:
        store.close()
    report = build_trust_report(table, args.series, as_of=args.as_of, config=config)
    if report.n_periods == 0:
        print(f"no data found for series {args.series!r}: check the series and the data folder")
        return 2
    text = (
        render_trust_markdown(report)
        if args.format == "markdown"
        else json.dumps(trust_report_to_dict(report), indent=2) + "\n"
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
        print(f"written to {args.out}")
    else:
        print(text)
    return 0


def _load_pipeline(spec: str, extra_paths: list[Path]) -> Pipeline:
    module_name, separator, function_name = spec.partition(":")
    if not separator or not module_name or not function_name:
        raise ValueError(f"pipeline must look like module:function, got {spec!r}")
    for path in reversed(extra_paths):
        sys.path.insert(0, str(path.resolve()))
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"cannot import {module_name!r}: {exc}") from exc
    function = getattr(module, function_name, None)
    if not callable(function):
        raise ValueError(f"{module_name!r} has no function {function_name!r}")
    return function  # type: ignore[no-any-return]


def _leak_check(args: argparse.Namespace) -> int:
    if args.step_weeks < 1:
        print("invalid settings: step-weeks must be at least 1", file=sys.stderr)
        return 2
    if args.first_origin > args.last_origin:
        print("invalid settings: first-origin is after last-origin", file=sys.stderr)
        return 2
    try:
        pipeline = _load_pipeline(args.pipeline, args.pipeline_path)
    except ValueError as exc:
        print(f"invalid settings: {exc}", file=sys.stderr)
        return 2
    store = _open_store(args)
    if store is None:
        return 2
    origins = []
    current = args.first_origin
    while current <= args.last_origin:
        origins.append(current)
        current += timedelta(weeks=args.step_weeks)
    try:
        result = check_pipeline(store, args.series, pipeline, origins)
    finally:
        store.close()
    print(result.summary())
    return 0 if result.ok else 1
