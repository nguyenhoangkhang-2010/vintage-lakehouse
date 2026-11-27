"""Benchmark output: a Markdown report and a JSON-ready dictionary."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from vintage_lakehouse.benchmarks.suite import BenchmarkResults


def _ms(seconds: float) -> str:
    milliseconds = seconds * 1000.0
    return f"{milliseconds:.2f}" if milliseconds < 100 else f"{milliseconds:.0f}"


def _mb(size: int | None) -> str:
    return "n/a" if size is None else f"{size / 2**20:.2f}"


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def results_to_dict(results: BenchmarkResults) -> dict[str, Any]:
    """The results as plain data, ready for ``json.dumps``."""
    config = asdict(results.config)
    directory = results.config.directory
    config["directory"] = None if directory is None else str(directory)
    return {
        "config": config,
        "machine": results.machine,
        "latency": [asdict(p) for p in results.latency],
        "ingest": [
            {**asdict(p), "versions_per_second": p.versions_per_second} for p in results.ingest
        ],
        "storage": [
            {**asdict(p), "row_ratio": p.row_ratio, "byte_ratio": p.byte_ratio}
            for p in results.storage
        ],
        "fragmentation": [asdict(p) for p in results.fragmentation],
        "notes": results.notes,
        "seconds_total": results.seconds_total,
    }


_SWEEPS = (
    ("series", "Many series (one series is queried; the rest is other data in the table)"),
    ("history", "One series with a growing history"),
    ("real", "The stored series (real data)"),
)


def render_markdown(results: BenchmarkResults, *, generated_at: datetime | None = None) -> str:
    """Write the results as Markdown."""
    config = results.config
    machine = results.machine
    stamp = f"Generated {generated_at:%Y-%m-%d %H:%M} UTC. " if generated_at else ""
    memory = f"{machine['memory_gb']} GiB" if machine.get("memory_gb") else "memory unknown"
    packages = ", ".join(f"{k} {v}" for k, v in machine["packages"].items())
    parts = [
        "# Benchmarks",
        "",
        f"{stamp}Total run time {results.seconds_total:.0f} s. Method and limits: "
        "docs/adr/0013-benchmarks.md.",
        "",
        "## Machine",
        "",
        f"- {machine['os']}; {machine['cpu']}; {machine['cores']} logical cores; {memory}",
        f"- Python {machine['python']}; {packages}",
        f"- Synthetic weekly series with {config.versions_per_period} versions per period "
        f"(random doubles), seed {config.seed}. {config.queries} timed queries per point after "
        f"{config.warmup} warm-up queries. Iceberg: local SQLite catalog on the local disk.",
        "",
        "## Query latency",
        "",
        "Milliseconds per call, including the conversion of results to observations. "
        "`as_of_random` queries random release times of the series, `as_of_latest` the last "
        "release, `revisions` every version of one period.",
        "",
    ]
    for sweep, title in _SWEEPS:
        points = [p for p in results.latency if p.sweep == sweep]
        if not points:
            continue
        header = [
            "Store",
            "Query",
            "Series",
            "Periods",
            "Versions in table",
            "Rows returned",
            "median ms",
            "p95 ms",
        ]
        rows = [
            [
                p.store,
                p.query,
                str(p.n_series),
                str(p.n_periods),
                f"{p.versions:,}",
                str(p.returned),
                _ms(p.summary.median),
                _ms(p.summary.p95),
            ]
            for p in points
        ]
        parts += [f"### {title}", "", _table(header, rows), ""]
    ingest_rows = [
        [
            p.store,
            p.scenario,
            f"{p.versions:,}",
            f"{p.seconds:.2f}",
            f"{p.versions_per_second:,.0f}",
        ]
        for p in results.ingest
    ]
    storage_rows = [
        [
            p.label,
            f"{p.versions:,}",
            f"{p.snapshot_rows:,}",
            f"{p.row_ratio:.1f}",
            _mb(p.versions_parquet_bytes),
            _mb(p.snapshots_parquet_bytes),
            f"{p.byte_ratio:.1f}",
            _mb(p.iceberg_bytes),
        ]
        for p in results.storage
    ]
    fragmentation_rows = [
        [
            p.layout,
            str(p.commits),
            str(p.files),
            f"{p.versions:,}",
            f"{p.load_seconds:.1f}",
            _ms(p.summary.median),
            _ms(p.summary.p95),
        ]
        for p in results.fragmentation
    ]
    fragmentation_section = (
        [
            "## Fragmentation: the same data in many commits or in one",
            "",
            "Each `append` is one Iceberg commit and adds data files, and a query has to read "
            "the table's metadata for all of them. The same table is loaded as one commit per "
            "series and as a single commit; `as_of_latest` is timed for the first series.",
            "",
            _table(
                ["Layout", "Commits", "Data files", "Versions", "Load s", "median ms", "p95 ms"],
                fragmentation_rows,
            ),
            "",
        ]
        if fragmentation_rows
        else []
    )
    parts += [
        "## Ingestion",
        "",
        f"`append` of {config.ingest_batches} batches of one series each "
        f"({config.periods_per_series} periods, {config.versions_per_period} versions per "
        "period), into an empty table and into a table that already holds as many series.",
        "",
        _table(["Store", "Table", "Versions", "Seconds", "Versions per second"], ingest_rows),
        "",
        *fragmentation_section,
        "## Storage: all versions against a snapshot at every release",
        "",
        "A snapshot per release keeps the whole series as known at every distinct publication "
        "time. Both layouts are Parquet (snappy). The Iceberg column is the size of the "
        "versioned Iceberg table on disk, data and metadata files.",
        "",
        _table(
            [
                "Table",
                "Versions",
                "Snapshot rows",
                "Rows x",
                "Versions MB",
                "Snapshots MB",
                "Bytes x",
                "Iceberg MB",
            ],
            storage_rows,
        ),
        "",
        "## Reading these numbers",
        "",
        "- One machine, one run. They show how the cost grows, not what another machine will do.",
        "- Synthetic values are random doubles and compress badly; a real-data row, when present, "
        "shows how real values behave.",
        "- Local disk and a single writer. Remote object storage or concurrent writers would "
        "change every number.",
        *[f"- {note}" for note in results.notes],
        "",
    ]
    return "\n".join(parts)


def write_outputs(out_dir: Path, report: str, results_json: str) -> list[Path]:
    """Write ``report.md`` and ``results.json`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / "report.md", out_dir / "results.json"]
    paths[0].write_text(report, encoding="utf-8", newline="\n")
    paths[1].write_text(results_json, encoding="utf-8", newline="\n")
    return paths
