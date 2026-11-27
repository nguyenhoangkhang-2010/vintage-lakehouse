"""The benchmarks of ADR 0013: query latency, ingestion throughput and storage overhead."""

from __future__ import annotations

import random
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any

from vintage_lakehouse.benchmarks.data import (
    BASE,
    SyntheticSpec,
    generate,
    generate_series,
    report_time,
)
from vintage_lakehouse.benchmarks.machine import describe_machine
from vintage_lakehouse.benchmarks.stats import TimingSummary, measure, summarize_times
from vintage_lakehouse.benchmarks.storage import StoragePoint, storage_overhead
from vintage_lakehouse.storage.base import AsOfStore
from vintage_lakehouse.storage.memory import InMemoryStore

STORES = ("memory", "iceberg")
QUERIES = ("as_of_random", "as_of_latest", "revisions")


@dataclass(frozen=True)
class BenchConfig:
    """What to run. The defaults are the published run; ``quick`` is a few seconds."""

    stores: tuple[str, ...] = STORES
    series_sweep: tuple[int, ...] = (1, 10, 100)
    history_sweep: tuple[int, ...] = (400, 1600, 6400)
    periods_per_series: int = 400
    versions_per_period: int = 20
    queries: int = 30
    warmup: int = 3
    ingest_batches: int = 5
    storage_periods: tuple[int, ...] = (200, 400, 800, 1600)
    seed: int = 0
    directory: Path | None = None

    def __post_init__(self) -> None:
        unknown = [s for s in self.stores if s not in STORES]
        if unknown:
            raise ValueError(f"unknown store {unknown[0]!r}; choose from {STORES}")
        if not self.stores:
            raise ValueError("at least one store is required")
        for name in ("series_sweep", "history_sweep", "storage_periods"):
            values = getattr(self, name)
            if not values or any(v < 1 for v in values):
                raise ValueError(f"{name} must be a non-empty list of positive integers")
        for name, minimum in (
            ("periods_per_series", 1),
            ("versions_per_period", 1),
            ("queries", 1),
            ("warmup", 0),
            ("ingest_batches", 1),
        ):
            if getattr(self, name) < minimum:
                raise ValueError(f"{name} must be at least {minimum}")

    @classmethod
    def quick(cls, **overrides: Any) -> BenchConfig:
        """A preset that finishes in seconds, for trying the command."""
        fields: dict[str, Any] = {
            "series_sweep": (1, 4),
            "history_sweep": (60, 120),
            "periods_per_series": 60,
            "versions_per_period": 8,
            "queries": 8,
            "warmup": 1,
            "ingest_batches": 3,
            "storage_periods": (40, 80),
        }
        fields.update(overrides)
        return cls(**fields)


@dataclass(frozen=True)
class LatencyPoint:
    """Timing of one query on one table. ``returned`` is the rows the last call returned."""

    store: str
    sweep: str
    n_series: int
    n_periods: int
    versions: int
    query: str
    returned: int
    summary: TimingSummary


@dataclass(frozen=True)
class IngestPoint:
    """Throughput of ``append`` (``scenario`` is an empty or an already filled table)."""

    store: str
    scenario: str
    batches: int
    versions: int
    seconds: float

    @property
    def versions_per_second(self) -> float:
        """Versions stored per second."""
        return self.versions / self.seconds


@dataclass(frozen=True)
class FragmentationPoint:
    """The same data loaded as many commits or as one: files, load time and query latency."""

    layout: str
    commits: int
    files: int
    versions: int
    load_seconds: float
    summary: TimingSummary


@dataclass(frozen=True)
class BenchmarkResults:
    """Everything a run measured, with the machine and configuration it ran on."""

    config: BenchConfig
    machine: dict[str, Any]
    latency: list[LatencyPoint]
    ingest: list[IngestPoint]
    storage: list[StoragePoint]
    notes: list[str]
    seconds_total: float
    fragmentation: list[FragmentationPoint] = field(default_factory=list)


class _Workspace:
    """Opens stores (in-memory, or Iceberg in a temporary folder) and cleans up after itself."""

    def __init__(self, directory: Path | None) -> None:
        self._owned = directory is None
        self.root = Path(tempfile.mkdtemp(prefix="vl-bench-")) if directory is None else directory
        self.root.mkdir(parents=True, exist_ok=True)
        self._count = 0
        self._closers: list[Callable[[], None]] = []

    def open(self, kind: str) -> tuple[AsOfStore, Path | None]:
        if kind == "memory":
            return InMemoryStore(), None
        from vintage_lakehouse.storage.iceberg import IcebergStore

        self._count += 1
        folder = self.root / f"warehouse-{self._count}"
        store = IcebergStore.local(folder)
        self._closers.append(store.close)
        return store, folder

    def close(self) -> None:
        for closer in self._closers:
            closer()
        if self._owned:
            shutil.rmtree(self.root, ignore_errors=True)


def folder_bytes(folder: Path) -> int:
    """The total size of the files under a folder."""
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


def _fill(store: AsOfStore, spec: SyntheticSpec) -> None:
    for index in range(spec.n_series):
        store.append(generate_series(spec, index))


def _latency(
    workspace: _Workspace, kind: str, sweep: str, spec: SyntheticSpec, config: BenchConfig
) -> list[LatencyPoint]:
    store, _ = workspace.open(kind)
    _fill(store, spec)
    series_id = spec.series_ids()[0]
    releases = [report_time(r) for r in range(spec.n_periods)]
    rng = random.Random(config.seed)
    origins = iter([rng.choice(releases) for _ in range(config.warmup + config.queries)])
    latest = releases[-1]
    middle = BASE + timedelta(weeks=spec.n_periods // 2)

    def returned(call: Callable[[], list[Any]]) -> int:
        return len(call())

    timed: dict[str, tuple[list[float], int]] = {
        "as_of_random": (
            measure(
                lambda: store.as_of(series_id, next(origins)),
                repeats=config.queries,
                warmup=config.warmup,
            ),
            returned(lambda: store.as_of(series_id, releases[len(releases) // 2])),
        ),
        "as_of_latest": (
            measure(
                lambda: store.as_of(series_id, latest),
                repeats=config.queries,
                warmup=config.warmup,
            ),
            returned(lambda: store.as_of(series_id, latest)),
        ),
        "revisions": (
            measure(
                lambda: store.revisions(series_id, middle),
                repeats=config.queries,
                warmup=config.warmup,
            ),
            returned(lambda: store.revisions(series_id, middle)),
        ),
    }
    versions = spec.expected_versions()
    return [
        LatencyPoint(
            store=kind,
            sweep=sweep,
            n_series=spec.n_series,
            n_periods=spec.n_periods,
            versions=versions,
            query=query,
            returned=timed[query][1],
            summary=summarize_times(timed[query][0]),
        )
        for query in QUERIES
    ]


def _ingest(workspace: _Workspace, kind: str, config: BenchConfig) -> list[IngestPoint]:
    batches = config.ingest_batches
    spec = SyntheticSpec(
        2 * batches, config.periods_per_series, config.versions_per_period, config.seed
    )
    data = [generate_series(spec, i) for i in range(spec.n_series)]
    points: list[IngestPoint] = []
    for scenario, timed in (("empty", data[:batches]), ("prefilled", data[batches:])):
        store, _ = workspace.open(kind)
        if scenario == "prefilled":
            for batch in data[:batches]:
                store.append(batch)
        started = time.perf_counter()
        for batch in timed:
            store.append(batch)
        seconds = time.perf_counter() - started
        points.append(
            IngestPoint(kind, scenario, batches, sum(len(b) for b in timed), max(seconds, 1e-9))
        )
    return points


def _storage(
    workspace: _Workspace, config: BenchConfig, periods: int, with_iceberg: bool
) -> StoragePoint:
    spec = SyntheticSpec(1, periods, config.versions_per_period, config.seed)
    observations = generate(spec)
    size: int | None = None
    if with_iceberg:
        store, folder = workspace.open("iceberg")
        store.append(observations)
        assert folder is not None
        size = folder_bytes(folder)
    return storage_overhead(observations, label=f"synthetic, {periods} periods", iceberg_bytes=size)


def _fragmentation(workspace: _Workspace, config: BenchConfig) -> list[FragmentationPoint]:
    """Load the same table as one commit per series and as a single commit, and time a query."""
    spec = SyntheticSpec(
        max(config.series_sweep),
        config.periods_per_series,
        config.versions_per_period,
        config.seed,
    )
    batches = [generate_series(spec, i) for i in range(spec.n_series)]
    latest = report_time(spec.n_periods - 1)
    points: list[FragmentationPoint] = []
    for layout, groups in (
        ("one commit per series", batches),
        ("one commit for all", [[o for batch in batches for o in batch]]),
    ):
        store, _ = workspace.open("iceberg")
        started = time.perf_counter()
        for group in groups:
            store.append(group)
        load = time.perf_counter() - started
        samples = measure(
            partial(store.as_of, spec.series_ids()[0], latest),
            repeats=config.queries,
            warmup=config.warmup,
        )
        points.append(
            FragmentationPoint(
                layout=layout,
                commits=len(groups),
                files=store.file_count(),  # type: ignore[attr-defined]
                versions=spec.expected_versions(),
                load_seconds=max(load, 1e-9),
                summary=summarize_times(samples),
            )
        )
    return points


def run_benchmarks(
    config: BenchConfig, *, progress: Callable[[str], None] | None = None
) -> BenchmarkResults:
    """Run the three benchmarks for every configured store."""
    say = progress or (lambda message: None)
    started = time.perf_counter()
    workspace = _Workspace(config.directory)
    latency: list[LatencyPoint] = []
    ingest: list[IngestPoint] = []
    storage: list[StoragePoint] = []
    fragmentation: list[FragmentationPoint] = []
    try:
        for kind in config.stores:
            for n_series in config.series_sweep:
                say(f"latency, {kind}: {n_series} series of {config.periods_per_series} periods")
                spec = SyntheticSpec(
                    n_series, config.periods_per_series, config.versions_per_period, config.seed
                )
                latency += _latency(workspace, kind, "series", spec, config)
            for periods in config.history_sweep:
                say(f"latency, {kind}: one series of {periods} periods")
                spec = SyntheticSpec(1, periods, config.versions_per_period, config.seed)
                latency += _latency(workspace, kind, "history", spec, config)
            say(f"ingestion, {kind}")
            ingest += _ingest(workspace, kind, config)
        if "iceberg" in config.stores:
            say("fragmentation, iceberg")
            fragmentation = _fragmentation(workspace, config)
        for periods in config.storage_periods:
            say(f"storage, {periods} periods")
            storage.append(_storage(workspace, config, periods, "iceberg" in config.stores))
    finally:
        workspace.close()
    return BenchmarkResults(
        config=config,
        machine=describe_machine(),
        latency=latency,
        ingest=ingest,
        storage=storage,
        notes=[],
        seconds_total=time.perf_counter() - started,
        fragmentation=fragmentation,
    )


def measure_real(
    store: AsOfStore,
    series_id: str,
    kind: str,
    config: BenchConfig,
    *,
    warehouse: Path | None = None,
) -> tuple[list[LatencyPoint], StoragePoint]:
    """Latency and storage overhead on an existing store (the real data), for one series.

    Loads every version of the series once (which can take a minute on the national series),
    then times the same three queries as the synthetic runs and compares the real versions with
    a snapshot at every release. ``warehouse`` is the folder whose size is reported as the
    Iceberg size, when the store is one.
    """
    from vintage_lakehouse.audit.leak_check import FAR_FUTURE, load_versions

    versions = load_versions(store, series_id, FAR_FUTURE)
    if not versions:
        raise ValueError(f"no data found for series {series_id!r}")
    releases = sorted({v.knowledge_time for v in versions})
    periods = sorted({v.event_time for v in versions})
    rng = random.Random(config.seed)
    origins = iter([rng.choice(releases) for _ in range(config.warmup + config.queries)])
    latest, middle = releases[-1], periods[len(periods) // 2]
    calls: dict[str, Callable[[], list[Any]]] = {
        "as_of_random": lambda: store.as_of(series_id, next(origins)),
        "as_of_latest": lambda: store.as_of(series_id, latest),
        "revisions": lambda: store.revisions(series_id, middle),
    }
    points = []
    for query in QUERIES:
        samples = measure(calls[query], repeats=config.queries, warmup=config.warmup)
        returned = len(
            store.as_of(series_id, releases[len(releases) // 2])
            if query == "as_of_random"
            else calls[query]()
        )
        points.append(
            LatencyPoint(
                store=kind,
                sweep="real",
                n_series=1,
                n_periods=len(periods),
                versions=len(versions),
                query=query,
                returned=returned,
                summary=summarize_times(samples),
            )
        )
    size = folder_bytes(warehouse) if warehouse is not None else None
    point = storage_overhead(versions, label=f"real data: {series_id}", iceberg_bytes=size)
    return points, point
