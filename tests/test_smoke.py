"""Smoke tests for the project skeleton."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from vintage_lakehouse import __version__
from vintage_lakehouse.cli import main
from vintage_lakehouse.config import DEFAULT_DATA_DIR, ENV_DATA_DIR, Settings

SUBPACKAGES = ["storage", "ingestion", "replay", "forecast", "backfill", "audit", "api"]


def test_version_has_three_numeric_parts() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)


def test_cli_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_cli_without_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage:" in capsys.readouterr().out


def test_cli_info_prints_data_dir(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, "somewhere")
    assert main(["info"]) == 0
    assert "somewhere" in capsys.readouterr().out


def test_settings_default_data_dir() -> None:
    assert Settings.from_env({}).data_dir == Path(DEFAULT_DATA_DIR)


def test_settings_reads_environment() -> None:
    assert Settings.from_env({ENV_DATA_DIR: "custom"}).data_dir == Path("custom")


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackages_are_importable(name: str) -> None:
    assert importlib.import_module(f"vintage_lakehouse.{name}") is not None
