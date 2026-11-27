"""Storage accounting: all versions against a full snapshot at every release.

Both layouts are written with the same Parquet writer and compression, so the byte counts are
comparable. A *snapshot per release* keeps, for every distinct knowledge_time of a series, the
whole series as known then; that is what one stores without a bitemporal model.
"""

from __future__ import annotations

import io
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

import pyarrow as pa
import pyarrow.parquet as pq

from vintage_lakehouse.storage.model import Observation


@dataclass(frozen=True)
class StoragePoint:
    """Rows and Parquet bytes of the two layouts for one table."""

    label: str
    series: int
    periods: int
    versions: int
    snapshot_rows: int
    versions_parquet_bytes: int
    snapshots_parquet_bytes: int
    iceberg_bytes: int | None

    @property
    def row_ratio(self) -> float:
        """Snapshot rows per version row."""
        return self.snapshot_rows / self.versions

    @property
    def byte_ratio(self) -> float:
        """Snapshot Parquet bytes per versions Parquet byte."""
        return self.snapshots_parquet_bytes / self.versions_parquet_bytes


def _by_series(observations: Sequence[Observation]) -> dict[str, list[Observation]]:
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.series_id].append(observation)
    return grouped


def _snapshots(observations: Sequence[Observation]) -> list[tuple[str, datetime, date, float]]:
    """(series, snapshot time, period, value) for the full series at every release."""
    rows: list[tuple[str, datetime, date, float]] = []
    for series_id, versions in _by_series(observations).items():
        by_period: dict[date, list[Observation]] = defaultdict(list)
        for version in versions:
            by_period[version.event_time].append(version)
        periods = []
        for period, history in sorted(by_period.items()):
            history.sort(key=lambda o: o.knowledge_time)
            periods.append(
                (period, [v.knowledge_time for v in history], [v.value for v in history])
            )
        for release in sorted({v.knowledge_time for v in versions}):
            for period, times, values in periods:
                position = bisect_right(times, release)
                if position:
                    rows.append((series_id, release, period, values[position - 1]))
    return rows


def snapshot_rows(observations: Sequence[Observation]) -> int:
    """How many rows a full snapshot at every release would hold."""
    total = 0
    for versions in _by_series(observations).values():
        first_release: dict[date, datetime] = {}
        for version in versions:
            known = first_release.get(version.event_time)
            if known is None or version.knowledge_time < known:
                first_release[version.event_time] = version.knowledge_time
        firsts = sorted(first_release.values())
        releases = sorted({v.knowledge_time for v in versions})
        index = 0
        for release in releases:
            while index < len(firsts) and firsts[index] <= release:
                index += 1
            total += index
    return total


def versions_table(observations: Sequence[Observation]) -> pa.Table:
    """The versions as an Arrow table."""
    return pa.table(
        {
            "series_id": [o.series_id for o in observations],
            "event_time": [o.event_time for o in observations],
            "knowledge_time": [o.knowledge_time for o in observations],
            "value": [o.value for o in observations],
        }
    )


def snapshots_table(observations: Sequence[Observation]) -> pa.Table:
    """The full series at every release as an Arrow table."""
    rows = _snapshots(observations)
    return pa.table(
        {
            "series_id": [r[0] for r in rows],
            "snapshot_time": [r[1] for r in rows],
            "event_time": [r[2] for r in rows],
            "value": [r[3] for r in rows],
        }
    )


def parquet_bytes(table: pa.Table) -> int:
    """The size of a table written as Parquet with the default (snappy) compression."""
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.tell()


def storage_overhead(
    observations: Sequence[Observation], *, label: str, iceberg_bytes: int | None = None
) -> StoragePoint:
    """Compare the layouts for one table."""
    versions = versions_table(observations)
    snapshots = snapshots_table(observations)
    grouped = _by_series(observations)
    periods = max((len({o.event_time for o in v}) for v in grouped.values()), default=0)
    return StoragePoint(
        label=label,
        series=len(grouped),
        periods=periods,
        versions=len(observations),
        snapshot_rows=snapshots.num_rows,
        versions_parquet_bytes=parquet_bytes(versions),
        snapshots_parquet_bytes=parquet_bytes(snapshots),
        iceberg_bytes=iceberg_bytes,
    )
