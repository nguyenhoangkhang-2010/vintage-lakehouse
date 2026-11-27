"""Tests for the benchmark suite: data, statistics, storage accounting and a tiny end-to-end run.

Timings are never asserted (they depend on the machine); structure and counts are.
"""

from __future__ import annotations

import json
from datetime import timedelta
from itertools import pairwise

import pytest

from backfill_fixture import revised_series
from vintage_lakehouse.benchmarks import (
    BenchConfig,
    SyntheticSpec,
    describe_machine,
    generate,
    measure,
    render_markdown,
    results_to_dict,
    run_benchmarks,
    snapshot_rows,
    storage_overhead,
    summarize_times,
)
from vintage_lakehouse.benchmarks.suite import measure_real
from vintage_lakehouse.storage import InMemoryStore

# --- synthetic data ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n_periods", "versions", "expected"),
    [(5, 3, 12), (3, 5, 6), (10, 1, 10), (4, 4, 10), (1, 20, 1)],
)
def test_the_version_count_matches_the_formula(
    n_periods: int, versions: int, expected: int
) -> None:
    spec = SyntheticSpec(n_series=2, n_periods=n_periods, versions_per_period=versions)
    assert spec.expected_versions() == 2 * expected
    assert len(generate(spec)) == 2 * expected


def test_generation_is_deterministic_and_seeded() -> None:
    spec = SyntheticSpec(n_series=2, n_periods=20, versions_per_period=4, seed=3)
    assert generate(spec) == generate(spec)
    other = SyntheticSpec(n_series=2, n_periods=20, versions_per_period=4, seed=4)
    assert [o.value for o in generate(spec)] != [o.value for o in generate(other)]


def test_series_are_distinct_and_versions_follow_the_reports() -> None:
    spec = SyntheticSpec(n_series=3, n_periods=6, versions_per_period=3)
    observations = generate(spec)
    assert {o.series_id for o in observations} == set(spec.series_ids())
    assert len(set(spec.series_ids())) == 3
    store = InMemoryStore()
    store.append(observations)  # valid and conflict-free: every key is unique
    first = spec.series_ids()[0]
    latest = store.as_of(first, max(o.knowledge_time for o in observations))
    assert len(latest) == 6
    versions = store.revisions(first, latest[0].event_time)
    assert len(versions) == 3
    gaps = [b.knowledge_time - a.knowledge_time for a, b in pairwise(versions)]
    assert gaps == [timedelta(days=7)] * 2


@pytest.mark.parametrize(
    "overrides",
    [{"n_series": 0}, {"n_periods": 0}, {"versions_per_period": 0}],
)
def test_invalid_specs_are_rejected(overrides: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        SyntheticSpec(**overrides)


# --- statistics ---------------------------------------------------------------------------


def test_summaries_are_hand_computed() -> None:
    s = summarize_times([5.0, 1.0, 3.0, 2.0, 4.0])
    assert (s.n, s.min, s.median, s.max, s.mean) == (5, 1.0, 3.0, 5.0, 3.0)
    assert s.p95 == pytest.approx(4.8)
    one = summarize_times([2.5])
    assert (one.median, one.p95, one.min, one.max) == (2.5, 2.5, 2.5, 2.5)


def test_an_empty_sample_cannot_be_summarised() -> None:
    with pytest.raises(ValueError, match="no timings"):
        summarize_times([])


def test_measure_runs_warmups_without_recording_them() -> None:
    calls: list[int] = []
    samples = measure(lambda: calls.append(1), repeats=4, warmup=2)
    assert len(calls) == 6 and len(samples) == 4 and all(s >= 0.0 for s in samples)
    with pytest.raises(ValueError, match="repeats"):
        measure(lambda: None, repeats=0)


# --- storage accounting -------------------------------------------------------------------


def test_snapshot_rows_are_hand_computed() -> None:
    # 5 weeks: the snapshot at report r holds weeks 0..r, so 1 + 2 + 3 + 4 + 5 rows
    assert snapshot_rows(revised_series(5, [1.0] * 5)) == 15
    two = [*revised_series(5, [1.0] * 5), *revised_series(5, [1.0] * 5, series="other")]
    assert snapshot_rows(two) == 30
    assert snapshot_rows([]) == 0


def test_a_snapshot_per_release_costs_far_more_than_the_versions() -> None:
    observations = generate(SyntheticSpec(n_series=1, n_periods=200, versions_per_period=5))
    point = storage_overhead(observations, label="synthetic")
    assert point.versions == len(observations) == 5 * 196 + 10
    assert point.snapshot_rows == 200 * 201 // 2  # 1 + 2 + ... + 200
    assert point.snapshot_rows > 10 * point.versions
    assert point.versions_parquet_bytes > 0
    assert point.snapshots_parquet_bytes > point.versions_parquet_bytes
    assert point.row_ratio == pytest.approx(point.snapshot_rows / point.versions)
    assert point.byte_ratio == pytest.approx(
        point.snapshots_parquet_bytes / point.versions_parquet_bytes
    )
    assert point.iceberg_bytes is None


def test_the_machine_is_described() -> None:
    machine = describe_machine()
    for key in ("os", "cpu", "cores", "python", "packages"):
        assert machine[key]
    assert "memory_gb" in machine


# --- a tiny end-to-end run ----------------------------------------------------------------


def tiny(**overrides: object) -> BenchConfig:
    fields: dict[str, object] = {
        "stores": ("memory",),
        "series_sweep": (1, 2),
        "history_sweep": (12, 24),
        "periods_per_series": 12,
        "versions_per_period": 3,
        "queries": 4,
        "warmup": 1,
        "ingest_batches": 3,
        "storage_periods": (10, 20),
    }
    fields.update(overrides)
    return BenchConfig(**fields)  # type: ignore[arg-type]


def test_every_benchmark_runs_and_gives_complete_results() -> None:
    config = tiny()
    results = run_benchmarks(config)
    latency = results.latency
    assert len(latency) == 1 * (2 + 2) * 3
    assert {p.query for p in latency} == {"as_of_random", "as_of_latest", "revisions"}
    assert {p.sweep for p in latency} == {"series", "history"}
    for p in latency:
        assert p.summary.n == 4
        assert p.versions == SyntheticSpec(p.n_series, p.n_periods, 3).expected_versions()
    latest = [p for p in latency if p.query == "as_of_latest"]
    assert all(p.returned == p.n_periods for p in latest)  # it measured the right thing
    assert {p.scenario for p in results.ingest} == {"empty", "prefilled"}
    assert all(
        p.versions > 0 and p.seconds > 0 and p.versions_per_second > 0 for p in results.ingest
    )
    assert [p.periods for p in results.storage] == [10, 20]
    assert results.machine["python"] and results.seconds_total > 0 and results.notes == []


def test_the_iceberg_store_is_benchmarked_and_its_size_on_disk_reported() -> None:
    pytest.importorskip("vintage_lakehouse.storage.iceberg")
    results = run_benchmarks(
        tiny(stores=("memory", "iceberg"), series_sweep=(1,), history_sweep=(12,))
    )
    assert {p.store for p in results.latency} == {"memory", "iceberg"}
    assert {p.store for p in results.ingest} == {"memory", "iceberg"}
    iceberg = [p for p in results.latency if p.store == "iceberg" and p.query == "as_of_latest"]
    assert iceberg and all(p.returned == p.n_periods for p in iceberg)
    assert all(p.iceberg_bytes and p.iceberg_bytes > 0 for p in results.storage)
    layouts = {p.layout: p for p in results.fragmentation}
    assert set(layouts) == {"one commit per series", "one commit for all"}
    assert layouts["one commit per series"].commits == 1  # series_sweep is (1,) in this run


def test_a_missing_store_is_reported_not_hidden() -> None:
    with pytest.raises(ValueError, match="unknown store"):
        tiny(stores=("memory", "postgres"))


def test_configuration_is_validated() -> None:
    for overrides in (
        {"stores": ()},
        {"queries": 0},
        {"warmup": -1},
        {"series_sweep": ()},
        {"history_sweep": (0,)},
        {"ingest_batches": 0},
        {"versions_per_period": 0},
    ):
        with pytest.raises(ValueError):
            tiny(**overrides)


def test_two_runs_generate_the_same_data_and_counts() -> None:
    a, b = run_benchmarks(tiny()), run_benchmarks(tiny())
    assert [(p.store, p.sweep, p.query, p.versions) for p in a.latency] == [
        (p.store, p.sweep, p.query, p.versions) for p in b.latency
    ]
    assert [(p.snapshot_rows, p.versions) for p in a.storage] == [
        (p.snapshot_rows, p.versions) for p in b.storage
    ]


def test_the_report_and_the_json_carry_the_numbers() -> None:
    results = run_benchmarks(tiny())
    markdown = render_markdown(results)
    for heading in ("# Benchmarks", "## Machine", "## Query latency", "## Ingestion", "## Storage"):
        assert heading in markdown
    assert "median" in markdown and "p95" in markdown and "memory" in markdown
    data = json.loads(json.dumps(results_to_dict(results)))
    assert data["machine"]["python"] and data["config"]["seed"] == 0
    assert len(data["latency"]) == len(results.latency)
    assert data["storage"][0]["snapshot_rows"] == results.storage[0].snapshot_rows


def test_real_data_points_use_the_store_it_is_given(tmp_path: object) -> None:
    spec = SyntheticSpec(n_series=2, n_periods=30, versions_per_period=4)
    store = InMemoryStore()
    store.append(generate(spec))
    config = tiny()
    points, storage = measure_real(store, spec.series_ids()[0], "memory", config)
    assert [p.query for p in points] == ["as_of_random", "as_of_latest", "revisions"]
    assert all(p.sweep == "real" and p.n_periods == 30 for p in points)
    assert all(p.versions == 4 * 27 + 6 for p in points)  # only the queried series
    assert points[1].returned == 30 and points[2].returned == 4
    assert storage.versions == 4 * 27 + 6 and storage.label.startswith("real data")
    assert storage.snapshot_rows == 30 * 31 // 2 and storage.iceberg_bytes is None


def test_real_data_for_an_unknown_series_is_an_error() -> None:
    with pytest.raises(ValueError, match="no data found"):
        measure_real(InMemoryStore(), "nothing", "memory", tiny())


def test_the_report_has_a_table_for_the_real_series_when_it_was_measured() -> None:
    from dataclasses import replace

    spec = SyntheticSpec(n_series=1, n_periods=30, versions_per_period=4)
    store = InMemoryStore()
    store.append(generate(spec))
    config = tiny()
    points, storage = measure_real(store, spec.series_ids()[0], "memory", config)
    results = run_benchmarks(config)
    results = replace(
        results, latency=[*results.latency, *points], storage=[*results.storage, storage]
    )
    markdown = render_markdown(results)
    assert "### The stored series (real data)" in markdown
    assert "real data: bench/series-0000" in markdown
    assert "### The stored series" not in render_markdown(run_benchmarks(config))


def test_fragmentation_compares_many_commits_with_one() -> None:
    pytest.importorskip("vintage_lakehouse.storage.iceberg")
    results = run_benchmarks(tiny(stores=("iceberg",), series_sweep=(1, 3), history_sweep=(12,)))
    many, one = results.fragmentation
    assert (many.commits, one.commits) == (3, 1)
    assert many.files >= 3 and one.files == 1
    assert many.versions == one.versions == SyntheticSpec(3, 12, 3).expected_versions()
    assert many.summary.n == one.summary.n == 4
    assert "## Fragmentation" in render_markdown(results)
    assert len(results_to_dict(results)["fragmentation"]) == 2


def test_memory_only_runs_have_no_fragmentation_section() -> None:
    results = run_benchmarks(tiny())
    assert results.fragmentation == []
    assert "## Fragmentation" not in render_markdown(results)


def test_the_iceberg_store_counts_its_data_files() -> None:
    pytest.importorskip("vintage_lakehouse.storage.iceberg")
    import tempfile

    from vintage_lakehouse.storage.iceberg import IcebergStore

    with tempfile.TemporaryDirectory() as folder:
        store = IcebergStore.local(folder)
        try:
            assert store.file_count() == 0
            store.append(generate(SyntheticSpec(1, 6, 2)))
            assert store.file_count() == 1
            store.append(generate(SyntheticSpec(2, 6, 2))[-3:])
            assert store.file_count() == 2
        finally:
            store.close()
