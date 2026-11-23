"""End-to-end tests of ``vintage-lakehouse ingest`` with a real Iceberg store and a fake source."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fluview_fixture import fake_fetch, year_response
from vintage_lakehouse.cli import main
from vintage_lakehouse.ingestion import delphi
from vintage_lakehouse.ingestion.archive import RawArchive
from vintage_lakehouse.ingestion.errors import FetchError
from vintage_lakehouse.ingestion.lineage import LineageLog


@pytest.fixture(autouse=True)
def _require_iceberg() -> None:
    pytest.importorskip("vintage_lakehouse.storage.iceberg")


def ingest_args(data_dir: Path, *extra: str) -> list[str]:
    return [
        "ingest",
        "delphi-fluview",
        "--first-year",
        "2023",
        "--last-year",
        "2023",
        "--last-issue",
        "202352",
        "--data-dir",
        str(data_dir),
        *extra,
    ]


def test_a_first_run_stores_the_versions_and_reports_the_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(delphi, "http_get_bytes", fake_fetch())
    assert main(ingest_args(tmp_path)) == 0
    out = capsys.readouterr().out
    assert (
        "202301-202352: 12 rows -> 12 new versions (3 new periods, 6 revisions, 3 unchanged)" in out
    )
    assert "total: 12 rows, 12 new versions, 6 revisions, largest revision 0.1" in out


def test_running_again_stores_nothing_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(delphi, "http_get_bytes", fake_fetch())
    assert main(ingest_args(tmp_path)) == 0
    capsys.readouterr()

    assert main(ingest_args(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "12 rows -> 0 new versions" in out
    assert "12 already stored" in out

    reports = LineageLog(tmp_path / "lineage" / "ingestion.jsonl").read()
    assert [r.new_versions for r in reports] == [12, 0]
    assert [r.duplicates for r in reports] == [0, 12]
    # The same response is archived once, however many times it is fetched.
    assert len(RawArchive(tmp_path / "raw", "delphi-fluview/nat").entries()) == 1


def test_lineage_ties_each_run_to_the_archived_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(delphi, "http_get_bytes", fake_fetch())
    assert main(ingest_args(tmp_path)) == 0
    [report] = LineageLog(tmp_path / "lineage" / "ingestion.jsonl").read()
    archive = RawArchive(tmp_path / "raw", "delphi-fluview/nat")
    [entry] = archive.entries()
    assert report.raw_sha256 == entry.sha256
    assert archive.read(entry) == year_response(2023)
    assert report.status == "ok"


def test_the_archive_can_rebuild_a_store_without_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    online, offline = tmp_path / "online", tmp_path / "offline"
    monkeypatch.setattr(delphi, "http_get_bytes", fake_fetch())
    assert main(ingest_args(online)) == 0
    shutil.copytree(online / "raw", offline / "raw")
    capsys.readouterr()

    def no_network(url: str) -> bytes:
        raise AssertionError(f"the network must not be used: {url}")

    monkeypatch.setattr(delphi, "http_get_bytes", no_network)
    assert main(ingest_args(offline, "--from-archive")) == 0
    assert "12 rows -> 12 new versions" in capsys.readouterr().out


def test_a_source_that_rewrites_history_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(delphi, "http_get_bytes", fake_fetch())
    assert main(ingest_args(tmp_path)) == 0
    capsys.readouterr()

    monkeypatch.setattr(delphi, "http_get_bytes", fake_fetch(value_shift=1.0))
    assert main(ingest_args(tmp_path)) == 1
    assert "ingestion failed" in capsys.readouterr().err

    reports = LineageLog(tmp_path / "lineage" / "ingestion.jsonl").read()
    assert [r.status for r in reports] == ["ok", "failed"]
    assert reports[1].error is not None
    assert "already stored" in reports[1].error
    # Both versions of the response are kept, because the source changed what it says.
    assert len(RawArchive(tmp_path / "raw", "delphi-fluview/nat").entries()) == 2


def test_an_unreachable_source_exits_with_code_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def down(url: str) -> bytes:
        raise FetchError("network down")

    monkeypatch.setattr(delphi, "http_get_bytes", down)
    assert main(ingest_args(tmp_path)) == 2
    assert "ingestion could not run: network down" in capsys.readouterr().err


def test_bad_arguments_are_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(ingest_args(tmp_path, "--region", "../etc")) == 2
    assert "invalid region" in capsys.readouterr().err

    backwards = ingest_args(tmp_path)
    backwards[backwards.index("--first-year") + 1] = "2025"
    assert main(backwards) == 2
    assert "--first-year must not be after --last-year" in capsys.readouterr().err
