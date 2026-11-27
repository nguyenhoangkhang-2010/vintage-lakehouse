"""End-to-end tests of ``vintage-lakehouse audit ...`` on a small stored series."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from backfill_fixture import SERIES, revised_series, varying_truth
from vintage_lakehouse.cli import main

CLEAN = """\
def pipeline(store, series_id, origin):
    return store.as_of(series_id, origin)[-1].value
"""

LEAKY = """\
from datetime import datetime, timezone

def pipeline(store, series_id, origin):
    far = datetime(2200, 1, 1, tzinfo=timezone.utc)
    return store.as_of(series_id, far)[-1].value
"""


PIPELINE_MODULES = ("clean_mod", "leaky_mod")


@pytest.fixture(autouse=True)
def _clean_import_state() -> Iterator[None]:
    """Forget the pipeline modules and import folders a test added (and nothing else)."""
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in PIPELINE_MODULES:
        sys.modules.pop(name, None)


def seed_store(data_dir: Path) -> None:
    pytest.importorskip("vintage_lakehouse.storage.iceberg")
    from vintage_lakehouse.storage.iceberg import IcebergStore

    store = IcebergStore.local(data_dir / "warehouse")
    try:
        store.append(revised_series(130, varying_truth))
    finally:
        store.close()


def write_pipeline(folder: Path, name: str, source: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.py").write_text(source, encoding="utf-8")


# --- trust-report ----------------------------------------------------------------------


def test_trust_report_prints_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed_store(tmp_path / "data")
    argv = ["audit", "trust-report", "--data-dir", str(tmp_path / "data"), "--series", SERIES]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "# Trust report: rev" in out and "1.250" in out and "## Findings" in out


def test_trust_report_can_write_json(tmp_path: Path) -> None:
    seed_store(tmp_path / "data")
    target = tmp_path / "out" / "trust.json"
    argv = [
        "audit",
        "trust-report",
        "--data-dir",
        str(tmp_path / "data"),
        "--series",
        SERIES,
        "--format",
        "json",
        "--out",
        str(target),
    ]
    assert main(argv) == 0
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["series_id"] == SERIES and data["n_periods"] == 130


def test_trust_report_honours_as_of(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed_store(tmp_path / "data")
    argv = [
        "audit",
        "trust-report",
        "--data-dir",
        str(tmp_path / "data"),
        "--series",
        SERIES,
        "--as-of",
        "2023-12-31",
        "--format",
        "json",
    ]
    assert main(argv) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_periods"] < 130 and data["as_of"].startswith("2023-12-31")


def test_trust_report_for_an_unknown_series_exits_with_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_store(tmp_path / "data")
    argv = ["audit", "trust-report", "--data-dir", str(tmp_path / "data"), "--series", "no/such"]
    assert main(argv) == 2
    assert "no data found" in capsys.readouterr().out


def test_trust_report_rejects_a_bad_setting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_store(tmp_path / "data")
    argv = ["audit", "trust-report", "--data-dir", str(tmp_path / "data"), "--settle", "0"]
    assert main(argv) == 2
    assert "invalid settings" in capsys.readouterr().err


# --- lint --------------------------------------------------------------------------------


def test_lint_exits_one_and_lists_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_pipeline(tmp_path / "code", "bad", "x = s.shift(-1)\ny = s.bfill()\n")
    assert main(["audit", "lint", str(tmp_path / "code")]) == 1
    out = capsys.readouterr().out
    assert "bad.py:1:5: LA001" in out and "bad.py:2:5: LA002" in out
    assert "2 finding(s)" in out and "heuristic" in out


def test_lint_exits_zero_on_clean_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_pipeline(tmp_path / "code", "good", CLEAN)
    assert main(["audit", "lint", str(tmp_path / "code")]) == 0
    assert "does not prove" in capsys.readouterr().out


def test_lint_of_a_missing_path_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["audit", "lint", "no/such/place.py"]) == 2
    assert "no such file" in capsys.readouterr().err


def test_lint_flags_a_leak_written_inline(tmp_path: Path) -> None:
    source = (
        "def pipeline(store, series_id, origin):\n"
        "    return store.as_of(series_id, datetime(2200, 1, 1, tzinfo=UTC))[-1].value\n"
    )
    write_pipeline(tmp_path / "code", "inline", source)
    assert main(["audit", "lint", str(tmp_path / "code")]) == 1


def test_lint_misses_a_leak_that_goes_through_a_variable(tmp_path: Path) -> None:
    """The static check is a heuristic: this leak is only found by `leak-check`."""
    write_pipeline(tmp_path / "code", "leaky", LEAKY)
    assert main(["audit", "lint", str(tmp_path / "code")]) == 0


# --- leak-check ------------------------------------------------------------------------


def leak_args(tmp_path: Path, module: str, *extra: str) -> list[str]:
    return [
        "audit",
        "leak-check",
        "--pipeline",
        f"{module}:pipeline",
        "--pipeline-path",
        str(tmp_path / "code"),
        "--data-dir",
        str(tmp_path / "data"),
        "--series",
        SERIES,
        "--first-origin",
        "2023-06-04",
        "--last-origin",
        "2024-06-02",
        "--step-weeks",
        "26",
        *extra,
    ]


def test_leak_check_passes_a_clean_pipeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_store(tmp_path / "data")
    write_pipeline(tmp_path / "code", "clean_mod", CLEAN)
    assert main(leak_args(tmp_path, "clean_mod")) == 0
    assert "No look-ahead found" in capsys.readouterr().out


def test_leak_check_finds_the_seeded_leak(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_store(tmp_path / "data")
    write_pipeline(tmp_path / "code", "leaky_mod", LEAKY)
    assert main(leak_args(tmp_path, "leaky_mod")) == 1
    out = capsys.readouterr().out
    assert "LEAK" in out and "LEAK at 3 of 3 origins" in out


@pytest.mark.parametrize(
    "pipeline",
    ["no_colon", "missing_module:pipeline", "clean_mod:nothing", ":pipeline"],
)
def test_leak_check_rejects_a_bad_pipeline_spec(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], pipeline: str
) -> None:
    seed_store(tmp_path / "data")
    write_pipeline(tmp_path / "code", "clean_mod", CLEAN)
    argv = leak_args(tmp_path, "clean_mod")
    argv[argv.index("--pipeline") + 1] = pipeline
    assert main(argv) == 2
    assert "invalid settings" in capsys.readouterr().err


def test_leak_check_rejects_a_bad_range(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed_store(tmp_path / "data")
    write_pipeline(tmp_path / "code", "clean_mod", CLEAN)
    assert main(leak_args(tmp_path, "clean_mod", "--step-weeks", "0")) == 2
    assert main(leak_args(tmp_path, "clean_mod", "--first-origin", "2030-01-01")) == 2
    assert "invalid settings" in capsys.readouterr().err
