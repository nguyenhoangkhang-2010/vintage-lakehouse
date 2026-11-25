"""Benchmarks: query latency, ingestion throughput and storage overhead (ADR 0013)."""

from vintage_lakehouse.benchmarks.data import SyntheticSpec, generate, generate_series
from vintage_lakehouse.benchmarks.machine import describe_machine
from vintage_lakehouse.benchmarks.report import render_markdown, results_to_dict, write_outputs
from vintage_lakehouse.benchmarks.stats import TimingSummary, measure, summarize_times
from vintage_lakehouse.benchmarks.storage import (
    StoragePoint,
    snapshot_rows,
    storage_overhead,
)
from vintage_lakehouse.benchmarks.suite import (
    BenchConfig,
    BenchmarkResults,
    FragmentationPoint,
    IngestPoint,
    LatencyPoint,
    run_benchmarks,
)

__all__ = [
    "BenchConfig",
    "BenchmarkResults",
    "FragmentationPoint",
    "IngestPoint",
    "LatencyPoint",
    "StoragePoint",
    "SyntheticSpec",
    "TimingSummary",
    "describe_machine",
    "generate",
    "generate_series",
    "measure",
    "render_markdown",
    "results_to_dict",
    "run_benchmarks",
    "snapshot_rows",
    "storage_overhead",
    "summarize_times",
    "write_outputs",
]
