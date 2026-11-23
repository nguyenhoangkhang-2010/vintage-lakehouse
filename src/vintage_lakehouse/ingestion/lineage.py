"""Lineage records: what each ingestion run read, and what it changed.

One ``IngestReport`` is written per fetched chunk to an append-only JSON-lines log, so every
stored version can be traced to a request, a run and (through ``raw_sha256``) the archived bytes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IngestReport:
    """Outcome of ingesting one chunk of observations."""

    run_id: str
    source: str
    request: str
    started_at: datetime
    finished_at: datetime
    status: str  # "ok" or "failed"
    error: str | None
    rows_fetched: int
    vintages: int  # distinct knowledge_time values in the chunk
    new_versions: int  # rows actually stored
    duplicates: int  # versions that were already stored
    new_periods: int  # first version ever seen for an event_time
    revisions: int  # new version whose value differs from the previous one
    unchanged: int  # new version republished with the same value
    max_abs_revision: float | None
    raw_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict (datetimes as ISO strings)."""
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        data["finished_at"] = self.finished_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IngestReport:
        """Inverse of ``to_dict``."""
        values = dict(data)
        values["started_at"] = datetime.fromisoformat(values["started_at"])
        values["finished_at"] = datetime.fromisoformat(values["finished_at"])
        return cls(**values)


class LineageLog:
    """Append-only JSON-lines log of ingestion reports."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, report: IngestReport) -> None:
        """Add one report to the log."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(report.to_dict()) + "\n")

    def read(self) -> list[IngestReport]:
        """Return every report, oldest first."""
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [IngestReport.from_dict(json.loads(line)) for line in lines if line.strip()]
