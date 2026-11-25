"""End-to-end tests of ``vintage-lakehouse benchmark`` with the quick preset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backfill_fixture import SERIES, revised_series, varying_truth
from vintage_lakehouse.cli import main


def test_quick_memory_run_writes_report_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    assert main(["benchmark", "--quick", "--stores", "memory", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "# Benchmarks" in printed and "files written:" in printed
    data = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert data["machine"]["python"] and data["config"]["stores"] == ["memory"]
    assert {p["store"] for p in data["latency"]} == {"memory"}
    assert (out / "report.md").read_text(encoding="utf-8").startswith("# Benchmarks")


def test_quick_run_with_iceberg_reports_its_size_on_disk(tmp_path: Path) -> None:
    pytest.importorskip("vintage_lakehouse.storage.iceberg")
    out = tmp_path / "out"
    assert main(["benchmark", "--quick", "--out", str(out), "--queries", "3"]) == 0
    data = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert {p["store"] for p in data["latency"]} == {"memory", "iceberg"}
    assert all(p["iceberg_bytes"] > 0 for p in data["storage"])
    assert data["config"]["queries"] == 3


def test_from_store_adds_the_real_series(tmp_path: Path) -> None:
    pytest.importorskip("vintage_lakehouse.storage.iceberg")
    from vintage_lakehouse.storage.iceberg import IcebergStore

    store = IcebergStore.local(tmp_path / "data" / "warehouse")
    try:
        store.append(revised_series(40, varying_truth))
    finally:
        store.close()
    out = tmp_path / "out"
    argv = [
        "benchmark",
        "--quick",
        "--stores",
        "memory",
        "--from-store",
        "--series",
        SERIES,
        "--data-dir",
        str(tmp_path / "data"),
        "--out",
        str(out),
        "--queries",
        "3",
    ]
    assert main(argv) == 0
    data = json.loads((out / "results.json").read_text(encoding="utf-8"))
    real = [p for p in data["latency"] if p["sweep"] == "real"]
    assert len(real) == 3 and real[1]["returned"] == 40
    assert data["storage"][-1]["label"].startswith("real data")
    assert data["storage"][-1]["iceberg_bytes"] > 0
    assert any("real-data" in note for note in data["notes"])


def test_from_store_for_an_unknown_series_exits_with_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("vintage_lakehouse.storage.iceberg")
    argv = [
        "benchmark",
        "--quick",
        "--stores",
        "memory",
        "--from-store",
        "--series",
        "no/such",
        "--data-dir",
        str(tmp_path / "data"),
        "--out",
        str(tmp_path / "out"),
        "--queries",
        "2",
    ]
    assert main(argv) == 2
    assert "no data found" in capsys.readouterr().err
    assert not (tmp_path / "out" / "report.md").exists()


@pytest.mark.parametrize("extra", [["--stores", "postgres"], ["--stores", ""], ["--queries", "0"]])
def test_invalid_settings_exit_with_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    assert main(["benchmark", "--quick", "--out", str(tmp_path / "out"), *extra]) == 2
    assert "invalid settings" in capsys.readouterr().err
