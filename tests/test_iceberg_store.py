"""Runs the store contract against the Iceberg-backed store, plus Iceberg-specific checks."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from store_contract import FIRST, SECOND, SERIES, THIRD, StoreContract, observation, three_versions
from vintage_lakehouse.storage import AsOfStore

if TYPE_CHECKING:
    from vintage_lakehouse.storage.iceberg import IcebergStore
else:
    # Skips this whole module when the optional Iceberg dependencies are not installed.
    IcebergStore = pytest.importorskip("vintage_lakehouse.storage.iceberg").IcebergStore

UTC = timezone.utc


class TestIcebergStore(StoreContract):
    # Creating a table on disk costs tens of milliseconds, so use fewer generated examples.
    property_examples = 12

    @pytest.fixture(autouse=True)
    def _workspace(self, tmp_path: Path) -> None:
        self.root = tmp_path / "warehouse"
        self.created = 0

    def make_store(self) -> AsOfStore:
        self.created += 1
        return IcebergStore.local(self.root, identifier=f"vintage.table_{self.created}")


def test_data_survives_reopening_the_store(tmp_path: Path) -> None:
    first = IcebergStore.local(tmp_path / "wh")
    first.append(three_versions())

    reopened = IcebergStore.local(tmp_path / "wh")
    assert reopened.as_of(SERIES, THIRD) == first.as_of(SERIES, THIRD)
    assert len(reopened.revisions(SERIES, three_versions()[0].event_time)) == 3


def test_two_handles_on_one_table_see_each_others_writes(tmp_path: Path) -> None:
    writer = IcebergStore.local(tmp_path / "wh")
    reader = IcebergStore.local(tmp_path / "wh")
    assert reader.as_of(SERIES, THIRD) == []
    writer.append([observation(known=FIRST, value=1.0)])
    assert [o.value for o in reader.as_of(SERIES, THIRD)] == [1.0]
    writer.append([observation(known=SECOND, value=2.0)])
    assert [o.value for o in reader.as_of(SERIES, THIRD)] == [2.0]


def test_close_releases_the_catalog_so_the_warehouse_can_be_deleted(tmp_path: Path) -> None:
    root = tmp_path / "wh"
    store = IcebergStore.local(root)
    store.append(three_versions())
    assert store.as_of(SERIES, THIRD)
    store.close()
    shutil.rmtree(root)  # fails on Windows while the SQLite catalog file is still open
    assert not root.exists()


def snapshot_count(store: IcebergStore) -> int:
    store._table.refresh()
    return len(store._table.metadata.snapshots)


def test_one_batch_is_one_commit(tmp_path: Path) -> None:
    store = IcebergStore.local(tmp_path / "wh")
    store.append(three_versions())
    assert snapshot_count(store) == 1


def test_no_op_appends_create_no_snapshot(tmp_path: Path) -> None:
    store = IcebergStore.local(tmp_path / "wh")
    store.append(three_versions())
    store.append([])
    store.append(three_versions())
    store.append([observation(known=FIRST, value=4.00, ingest=datetime(2024, 1, 1, tzinfo=UTC))])
    assert snapshot_count(store) == 1
