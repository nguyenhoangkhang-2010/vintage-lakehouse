"""A store must keep working when the working directory changes.

Iceberg writes the locations of its files into the catalog. If the warehouse is given as a
relative path, those locations are relative too, and the store can then only be opened from the
folder where it was created. The store therefore always uses absolute paths.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from fluview_fixture import fake_fetch
from store_contract import SERIES, THIRD, three_versions
from vintage_lakehouse.cli import main
from vintage_lakehouse.ingestion import delphi

if TYPE_CHECKING:
    from vintage_lakehouse.storage.iceberg import IcebergStore
else:
    # Skips this whole module when the optional Iceberg dependencies are not installed.
    IcebergStore = pytest.importorskip("vintage_lakehouse.storage.iceberg").IcebergStore


FAR_FUTURE = datetime(2030, 1, 1, tzinfo=timezone.utc)


def elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Move the working directory to an unrelated folder."""
    other = tmp_path / "somewhere_else"
    other.mkdir()
    monkeypatch.chdir(other)


def test_a_relative_warehouse_still_opens_after_the_working_directory_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = IcebergStore.local("warehouse")  # relative, as the command-line default is
    store.append(three_versions())
    expected = store.as_of(SERIES, THIRD)
    store.close()

    elsewhere(tmp_path, monkeypatch)
    reopened = IcebergStore.local(tmp_path / "warehouse")
    try:
        assert reopened.as_of(SERIES, THIRD) == expected
        assert len(expected) == 1
    finally:
        reopened.close()


def test_the_catalog_records_absolute_file_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = IcebergStore.local("warehouse")
    store.append(three_versions())
    store.close()

    connection = sqlite3.connect(tmp_path / "warehouse" / "catalog.db")
    try:
        locations = [
            row[0] for row in connection.execute("select metadata_location from iceberg_tables")
        ]
    finally:
        connection.close()
    assert locations
    assert all(os.path.isabs(location) for location in locations)


def build_like_an_older_version(tmp_path: Path) -> None:
    """Create an empty warehouse the way the earlier code did: with a relative path."""
    from pyiceberg.catalog.sql import SqlCatalog

    from vintage_lakehouse.storage.iceberg import SCHEMA

    root = Path("warehouse")  # relative to the current working directory
    root.mkdir()
    catalog = SqlCatalog(
        "local", uri=f"sqlite:///{(root / 'catalog.db').as_posix()}", warehouse=str(root)
    )
    catalog.create_namespace("vintage")
    catalog.create_table("vintage.observations", schema=SCHEMA)
    catalog.engine.dispose()


def test_a_relative_store_from_an_older_version_gives_a_clear_error_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vintage_lakehouse.storage.iceberg import WarehouseLocationError

    monkeypatch.chdir(tmp_path)
    build_like_an_older_version(tmp_path)

    elsewhere(tmp_path, monkeypatch)
    with pytest.raises(WarehouseLocationError, match="relative path") as caught:
        IcebergStore.local(tmp_path / "warehouse")
    message = str(caught.value)
    assert str((tmp_path / "warehouse").resolve()) in message
    assert "--from-archive" in message


def test_a_relative_store_from_an_older_version_still_opens_in_its_own_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    build_like_an_older_version(tmp_path)
    store = IcebergStore.local("warehouse")
    try:
        assert store.as_of(SERIES, THIRD) == []
    finally:
        store.close()


def test_ingesting_with_a_relative_data_dir_leaves_a_store_that_opens_from_anywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(delphi, "http_get_bytes", fake_fetch())
    argv = [
        "ingest",
        "delphi-fluview",
        "--first-year",
        "2023",
        "--last-year",
        "2023",
        "--last-issue",
        "202352",
        "--data-dir",
        "data",  # relative, exactly as when run from the project folder
    ]
    assert main(argv) == 0

    elsewhere(tmp_path, monkeypatch)
    store = IcebergStore.local(tmp_path / "data" / "warehouse")
    try:
        known = store.as_of("delphi/fluview/nat/wili", FAR_FUTURE)
    finally:
        store.close()
    assert len(known) == 3  # weeks 1 to 3 of 2023 in the canned response
