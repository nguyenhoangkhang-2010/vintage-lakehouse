"""End-to-end tests of ``vintage-lakehouse experiment backfill-recovery`` on a small series."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from experiment_fixture import SERIES, at, revised_observations
from vintage_lakehouse.cli import main


@pytest.fixture(autouse=True)
def _require_iceberg() -> None:
    pytest.importorskip("vintage_lakehouse.storage.iceberg")


def seed_store(data_dir: Path) -> None:
    from vintage_lakehouse.storage.iceberg import IcebergStore

    store = IcebergStore.local(data_dir / "warehouse")
    try:
        store.append(revised_observations())
    finally:
        store.close()


def args(data_dir: Path, out: Path, *extra: str) -> list[str]:
    return [
        "experiment",
        "backfill-recovery",
        "--data-dir",
        str(data_dir),
        "--out",
        str(out),
        "--series",
        SERIES,
        "--models",
        "seasonal_naive",
        "--first-origin",
        at(130, 6).date().isoformat(),
        "--last-origin",
        at(139, 6).date().isoformat(),
        "--origin-step-weeks",
        "3",
        "--horizons",
        "1,2",
        "--evaluation-time",
        at(162).date().isoformat(),
        "--n-boot",
        "30",
        "--block-weeks",
        "6",
        *extra,
    ]


def test_the_command_runs_and_writes_its_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_store(tmp_path / "data")
    assert main(args(tmp_path / "data", tmp_path / "out")) == 0
    out = capsys.readouterr().out
    assert "corrected models in real time" in out
    assert "## Headline" in out
    assert "seasonal_naive" in out and "backfill-placebo" in out
    for name in (
        "report.md",
        "manifest.json",
        "recovery.csv",
        "variant_comparisons.csv",
        "leakage_gap.csv",
        "factors.csv",
        "scores.csv",
    ):
        assert (tmp_path / "out" / name).exists()


def test_running_it_twice_gives_identical_numbers(tmp_path: Path) -> None:
    seed_store(tmp_path / "data")
    assert main(args(tmp_path / "data", tmp_path / "first")) == 0
    assert main(args(tmp_path / "data", tmp_path / "second")) == 0
    for name in ("recovery.csv", "variant_comparisons.csv", "factors.csv", "scores.csv"):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_the_default_output_folder_does_not_collide_with_the_leakage_gap_experiment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_store(tmp_path / "data")
    monkeypatch.chdir(tmp_path)
    argv = args(tmp_path / "data", tmp_path / "unused")
    index = argv.index("--out")
    del argv[index : index + 2]
    assert main(argv) == 0
    assert (tmp_path / "results" / "backfill-recovery" / "report.md").exists()
    assert not (tmp_path / "results" / "report.md").exists()


def test_only_the_requested_variants_are_run(tmp_path: Path) -> None:
    seed_store(tmp_path / "data")
    assert main(args(tmp_path / "data", tmp_path / "out", "--variants", "backfill")) == 0
    with (tmp_path / "out" / "recovery.csv").open(encoding="utf-8", newline="") as handle:
        assert {r["variant"] for r in csv.DictReader(handle)} == {"backfill"}


@pytest.mark.parametrize(
    "extra",
    [
        ["--variants", "magic"],
        ["--variants", ""],
        ["--max-lag", "0"],
        ["--settle", "0"],
        ["--min-samples", "0"],
        ["--models", "magic"],
    ],
)
def test_invalid_settings_exit_with_code_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    seed_store(tmp_path / "data")
    assert main([*args(tmp_path / "data", tmp_path / "out"), *extra]) == 2
    assert "invalid experiment settings" in capsys.readouterr().err
    assert not (tmp_path / "out" / "report.md").exists()


def test_a_store_without_the_series_reports_that_nothing_was_scored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_store(tmp_path / "data")
    argv = args(tmp_path / "data", tmp_path / "out", "--series", "no/such/series")
    assert main(argv) == 2
    assert "no forecasts were scored" in capsys.readouterr().err
