"""Tests for the raw response archive."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vintage_lakehouse.ingestion.archive import ArchiveCorruptError, RawArchive

UTC = timezone.utc
WHEN = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


def test_save_then_read_returns_the_exact_bytes(tmp_path: Path) -> None:
    archive = RawArchive(tmp_path, "src")
    raw = b'{"result": 1}'
    entry = archive.save("http://example/a", raw, WHEN)
    assert entry.sha256 == hashlib.sha256(raw).hexdigest()
    assert entry.size == len(raw)
    assert entry.retrieved_at == WHEN
    assert archive.read(entry) == raw
    assert (tmp_path / "src" / f"{entry.sha256}.json").read_bytes() == raw


def test_an_empty_archive_has_no_entries(tmp_path: Path) -> None:
    assert RawArchive(tmp_path, "src").entries() == []


def test_saving_the_same_response_twice_is_a_no_op(tmp_path: Path) -> None:
    archive = RawArchive(tmp_path, "src")
    first = archive.save("http://example/a", b"one", WHEN)
    again = archive.save("http://example/a", b"one", WHEN + timedelta(days=1))
    assert again == first
    assert len(archive.entries()) == 1


def test_a_different_request_with_identical_bytes_shares_the_file(tmp_path: Path) -> None:
    archive = RawArchive(tmp_path, "src")
    archive.save("http://example/a", b"same", WHEN)
    archive.save("http://example/b", b"same", WHEN)
    assert [e.request for e in archive.entries()] == ["http://example/a", "http://example/b"]
    assert len(list((tmp_path / "src").glob("*.json"))) == 1


def test_a_source_that_changes_its_answer_is_archived_as_a_new_response(tmp_path: Path) -> None:
    archive = RawArchive(tmp_path, "src")
    old = archive.save("http://example/a", b"before", WHEN)
    new = archive.save("http://example/a", b"after", WHEN + timedelta(days=1))
    assert old.sha256 != new.sha256
    assert archive.read(old) == b"before"
    assert archive.read(new) == b"after"
    assert [e.sha256 for e in archive.entries()] == [old.sha256, new.sha256]


def test_entries_survive_reopening_in_order(tmp_path: Path) -> None:
    first = RawArchive(tmp_path, "src")
    saved = [first.save(f"http://example/{n}", f"body {n}".encode(), WHEN) for n in range(3)]
    assert RawArchive(tmp_path, "src").entries() == saved


def test_tampered_files_are_detected(tmp_path: Path) -> None:
    archive = RawArchive(tmp_path, "src")
    entry = archive.save("http://example/a", b"original", WHEN)
    (tmp_path / "src" / f"{entry.sha256}.json").write_bytes(b"tampered")
    with pytest.raises(ArchiveCorruptError, match="does not match"):
        archive.read(entry)


def test_retrieval_time_must_be_timezone_aware(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be timezone-aware"):
        RawArchive(tmp_path, "src").save("http://example/a", b"x", datetime(2026, 11, 23))


def test_retrieval_time_is_stored_in_utc(tmp_path: Path) -> None:
    local = datetime(2026, 11, 23, 16, 0, tzinfo=timezone(timedelta(hours=7)))
    entry = RawArchive(tmp_path, "src").save("http://example/a", b"x", local)
    assert entry.retrieved_at == WHEN
    assert entry.retrieved_at.utcoffset() == timedelta(0)


def test_sources_can_be_nested_in_subfolders(tmp_path: Path) -> None:
    archive = RawArchive(tmp_path, "delphi-fluview/nat")
    archive.save("http://example/a", b"x", WHEN)
    assert (tmp_path / "delphi-fluview" / "nat" / "index.jsonl").exists()
