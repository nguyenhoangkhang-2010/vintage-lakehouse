"""Immutable archive of raw source responses.

Every response is stored byte for byte under its SHA-256, with an append-only index that
records which request produced it and when. The archive is the dataset of record: if a source
disappears or changes, ingestion can be replayed offline from it, and every stored version can
be traced back to the exact bytes it came from (see docs/adr/0006).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from vintage_lakehouse.storage.model import to_utc

INDEX_NAME = "index.jsonl"


class ArchiveCorruptError(ValueError):
    """An archived file no longer matches the SHA-256 recorded for it."""


@dataclass(frozen=True)
class ArchiveEntry:
    """One archived response."""

    request: str
    sha256: str
    retrieved_at: datetime
    size: int


class RawArchive:
    """Append-only store of raw responses for one source."""

    def __init__(self, directory: str | Path, source_id: str) -> None:
        self.root = Path(directory) / source_id
        self._index = self.root / INDEX_NAME

    def _path(self, sha256: str) -> Path:
        return self.root / f"{sha256}.json"

    def entries(self) -> list[ArchiveEntry]:
        """Return all archived responses in the order they were first saved."""
        if not self._index.exists():
            return []
        entries: list[ArchiveEntry] = []
        for line in self._index.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            entries.append(
                ArchiveEntry(
                    request=data["request"],
                    sha256=data["sha256"],
                    retrieved_at=datetime.fromisoformat(data["retrieved_at"]),
                    size=int(data["size"]),
                )
            )
        return entries

    def save(self, request: str, raw: bytes, retrieved_at: datetime) -> ArchiveEntry:
        """Archive ``raw`` and return its entry. Saving the same response again is a no-op."""
        digest = hashlib.sha256(raw).hexdigest()
        for entry in self.entries():
            if entry.request == request and entry.sha256 == digest:
                return entry

        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(digest)
        if not path.exists():
            path.write_bytes(raw)
        entry = ArchiveEntry(
            request=request,
            sha256=digest,
            retrieved_at=to_utc(retrieved_at, "retrieved_at"),
            size=len(raw),
        )
        line = json.dumps(
            {
                "request": entry.request,
                "sha256": entry.sha256,
                "retrieved_at": entry.retrieved_at.isoformat(),
                "size": entry.size,
            }
        )
        with self._index.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        return entry

    def read(self, entry: ArchiveEntry) -> bytes:
        """Return the archived bytes, after checking them against the recorded SHA-256."""
        raw = self._path(entry.sha256).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry.sha256:
            raise ArchiveCorruptError(
                f"archived file for request {entry.request!r} does not match its recorded "
                f"sha256 {entry.sha256}"
            )
        return raw
