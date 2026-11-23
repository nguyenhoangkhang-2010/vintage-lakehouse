"""The replay must give the same answer whichever store holds the data."""

from __future__ import annotations

from pathlib import Path

import pytest

from replay_fixture import Constant, LastValue, config, flat_series
from vintage_lakehouse.replay import Forecaster, leakage_gap, run_replay
from vintage_lakehouse.storage import InMemoryStore


@pytest.fixture(autouse=True)
def _require_iceberg() -> None:
    pytest.importorskip("vintage_lakehouse.storage.iceberg")


def forecasters() -> list[Forecaster]:
    return [LastValue(), Constant("perfect", 10.0)]


def test_the_iceberg_store_replays_exactly_like_the_in_memory_store(tmp_path: Path) -> None:
    from vintage_lakehouse.storage.iceberg import IcebergStore

    observations = flat_series()
    memory = InMemoryStore()
    memory.append(observations)
    iceberg = IcebergStore.local(tmp_path / "warehouse")
    try:
        iceberg.append(observations)
        from_memory = run_replay(memory, forecasters(), config())
        from_iceberg = run_replay(iceberg, forecasters(), config())
    finally:
        iceberg.close()

    assert from_iceberg.rows == from_memory.rows
    assert from_iceberg.skipped == from_memory.skipped
    assert leakage_gap(from_iceberg.rows) == leakage_gap(from_memory.rows)
