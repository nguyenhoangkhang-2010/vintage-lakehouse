"""End-to-end tests of ``vintage-lakehouse experiment leakage-gap`` on a small stored series."""

from __future__ import annotations

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
        "leakage-gap",
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
    assert "replaying with T_eval" in out
    assert "## Headline" in out
    assert "files written:" in out
    for name in ("report.md", "manifest.json", "leakage_gap.csv", "scores.csv"):
        assert (tmp_path / "out" / name).exists()


def test_running_it_twice_gives_identical_numbers(tmp_path: Path) -> None:
    seed_store(tmp_path / "data")
    assert main(args(tmp_path / "data", tmp_path / "first")) == 0
    assert main(args(tmp_path / "data", tmp_path / "second")) == 0
    for name in (
        "leakage_gap.csv",
        "scores.csv",
        "model_comparisons.csv",
        "revision_size_groups.csv",
        "revision_size_correlation.csv",
    ):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_a_different_seed_changes_the_intervals_but_not_the_estimates(tmp_path: Path) -> None:
    import csv

    seed_store(tmp_path / "data")
    assert main(args(tmp_path / "data", tmp_path / "a", "--seed", "1")) == 0
    assert main(args(tmp_path / "data", tmp_path / "b", "--seed", "2")) == 0

    def pooled(out: Path) -> dict[str, str]:
        with (out / "leakage_gap.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return next(r for r in rows if r["horizon"] == "" and "all origins" in r["scenario"])

    a, b = pooled(tmp_path / "a"), pooled(tmp_path / "b")
    assert a["gap"] == b["gap"]
    assert a["n_pairs"] == b["n_pairs"]


@pytest.mark.parametrize(
    "extra",
    [
        ["--models", "magic"],
        ["--horizons", ""],
        ["--confidence", "2"],
        ["--n-boot", "0"],
        ["--evaluation-time", "2020-01-01"],
    ],
)
def test_invalid_settings_exit_with_code_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    seed_store(tmp_path / "data")
    argv = args(tmp_path / "data", tmp_path / "out")
    # a later option overrides an earlier one
    assert main([*argv, *extra]) == 2
    assert "invalid experiment settings" in capsys.readouterr().err
    assert not (tmp_path / "out" / "report.md").exists()


def test_a_store_without_the_series_reports_that_nothing_was_scored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_store(tmp_path / "data")
    argv = args(tmp_path / "data", tmp_path / "out", "--series", "no/such/series")
    assert main(argv) == 2
    assert "no forecasts were scored" in capsys.readouterr().err


def test_bad_dates_are_rejected_by_the_parser() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["experiment", "leakage-gap", "--first-origin", "not-a-date"])
    assert caught.value.code == 2
