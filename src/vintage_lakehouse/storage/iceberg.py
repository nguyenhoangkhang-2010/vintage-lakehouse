"""Iceberg-backed implementation of the ``AsOfStore`` contract.

Every version of every observation is a row of one Iceberg table. PyIceberg prunes the scan
(series and knowledge_time predicates are pushed down), and DuckDB picks the latest version
per event_time with a window query. Each ``append`` is one Iceberg commit, so a batch is
atomic.

Requires the optional dependencies: ``pip install "vintage-lakehouse[iceberg]"``.

Assumptions and limits (see docs/adr/0003-storage-and-query-stack.md):

- One writer at a time. The duplicate and conflict check reads the table before writing, so two
  concurrent writers could both pass the check.
- ``ingest_time`` is stored exactly as given. The store never stamps it.
- Timestamps are decoded through integer microseconds since the epoch, so no time zone
  database is involved.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
from pyiceberg.catalog import Catalog
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import (
    And,
    BooleanExpression,
    EqualTo,
    GreaterThanOrEqual,
    In,
    LessThanOrEqual,
)
from pyiceberg.schema import Schema
from pyiceberg.types import DateType, DoubleType, NestedField, StringType, TimestamptzType

from vintage_lakehouse.storage.base import ConflictError
from vintage_lakehouse.storage.model import Observation, ObservationKey, to_utc

# PyIceberg's expression classes are pydantic models. mypy synthesises an ``__init__`` for them
# that wrongly demands a bound term, although they accept plain column names at runtime (the
# tests exercise every one of them). Typing the constructors as ``Any`` keeps the rest of this
# module fully type-checked.
_EqualTo: Any = EqualTo
_In: Any = In
_GreaterThanOrEqual: Any = GreaterThanOrEqual
_LessThanOrEqual: Any = LessThanOrEqual

UTC = timezone.utc
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

DEFAULT_IDENTIFIER = "vintage.observations"

SCHEMA = Schema(
    NestedField(1, "series_id", StringType(), required=True),
    NestedField(2, "event_time", DateType(), required=True),
    NestedField(3, "knowledge_time", TimestamptzType(), required=True),
    NestedField(4, "value", DoubleType(), required=True),
    NestedField(5, "source", StringType(), required=True),
    NestedField(6, "ingest_time", TimestamptzType(), required=False),
)

_TIMESTAMP_COLUMNS = ("knowledge_time", "ingest_time")

# The knowledge_time predicate is repeated in SQL on purpose: PyIceberg's pushdown is an
# optimisation, and the answer must not depend on it being exact.
_AS_OF_SQL = """
SELECT series_id, event_time, knowledge_time, value, source, ingest_time
FROM versions
WHERE series_id = ? AND epoch_us(knowledge_time) <= ?
QUALIFY row_number() OVER (PARTITION BY event_time ORDER BY knowledge_time DESC) = 1
ORDER BY event_time
"""


def _micros(moment: datetime) -> int:
    delta = moment - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _from_micros(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


def _decode(table: pa.Table) -> list[dict[str, Any]]:
    """Turn an Arrow table into plain Python rows, with timestamps as UTC datetimes.

    Timestamp columns are cast to integer microseconds first, which avoids any dependency on a
    time zone database.
    """
    for name in _TIMESTAMP_COLUMNS:
        if name in table.column_names:
            index = table.column_names.index(name)
            table = table.set_column(index, name, pc.cast(table[name], pa.int64()))
    rows: list[dict[str, Any]] = table.to_pylist()
    for row in rows:
        for name in _TIMESTAMP_COLUMNS:
            if row.get(name) is not None:
                row[name] = _from_micros(row[name])
    return rows


def _to_observations(table: pa.Table) -> list[Observation]:
    return [
        Observation(
            series_id=row["series_id"],
            event_time=row["event_time"],
            knowledge_time=row["knowledge_time"],
            value=row["value"],
            source=row["source"],
            ingest_time=row["ingest_time"],
        )
        for row in _decode(table)
    ]


class IcebergStore:
    """An ``AsOfStore`` backed by an Iceberg table."""

    def __init__(self, catalog: Catalog, *, identifier: str = DEFAULT_IDENTIFIER) -> None:
        namespace, _, _ = identifier.rpartition(".")
        if namespace:
            catalog.create_namespace_if_not_exists(namespace)
        self._catalog = catalog
        self._table = catalog.create_table_if_not_exists(identifier, schema=SCHEMA)
        self._arrow_schema = SCHEMA.as_arrow()

    def close(self) -> None:
        """Release the catalog's database connection.

        Needed on Windows before deleting the warehouse directory: an open SQLite catalog file
        cannot be removed. Catalogs without a SQL engine need nothing, so this is then a no-op.
        """
        engine = getattr(self._catalog, "engine", None)
        if engine is not None:
            engine.dispose()

    @classmethod
    def local(cls, warehouse: str | Path, *, identifier: str = DEFAULT_IDENTIFIER) -> IcebergStore:
        """Open (or create) a store in a local directory, using a SQLite catalog.

        The warehouse is passed to PyIceberg as a plain path. A ``file:///C:/...`` URI is
        misread on Windows.
        """
        root = Path(warehouse)
        root.mkdir(parents=True, exist_ok=True)
        catalog = SqlCatalog(
            "local",
            uri=f"sqlite:///{(root / 'catalog.db').as_posix()}",
            warehouse=str(root),
        )
        return cls(catalog, identifier=identifier)

    # --- reading --------------------------------------------------------------------

    def _scan(self, row_filter: BooleanExpression, columns: tuple[str, ...] = ("*",)) -> pa.Table:
        self._table.refresh()
        return self._table.scan(row_filter=row_filter, selected_fields=columns).to_arrow()

    def as_of(self, series_id: str, at: datetime) -> list[Observation]:
        """Return the latest version known at ``at`` for each event_time."""
        moment = to_utc(at, "at")
        scanned = self._scan(
            And(
                _EqualTo("series_id", series_id),
                _LessThanOrEqual("knowledge_time", moment.isoformat()),
            )
        )
        if scanned.num_rows == 0:
            return []
        connection = duckdb.connect()
        try:
            connection.register("versions", scanned)
            latest = connection.execute(_AS_OF_SQL, [series_id, _micros(moment)]).to_arrow_table()
        finally:
            connection.close()
        return _to_observations(latest)

    def revisions(self, series_id: str, event_time: date) -> list[Observation]:
        """Return all versions of one event_time, oldest first."""
        scanned = self._scan(
            And(_EqualTo("series_id", series_id), _EqualTo("event_time", event_time.isoformat()))
        )
        return sorted(_to_observations(scanned), key=lambda o: o.knowledge_time)

    # --- writing --------------------------------------------------------------------

    def _stored(self, batch: Iterable[Observation]) -> dict[ObservationKey, tuple[float, str]]:
        """Return (value, source) of already stored versions in the same range as ``batch``."""
        items = list(batch)
        scanned = self._scan(
            And(
                _In("series_id", sorted({o.series_id for o in items})),
                _GreaterThanOrEqual("event_time", min(o.event_time for o in items).isoformat()),
                _LessThanOrEqual("event_time", max(o.event_time for o in items).isoformat()),
            ),
            columns=("series_id", "event_time", "knowledge_time", "value", "source"),
        )
        return {
            ObservationKey(r["series_id"], r["event_time"], r["knowledge_time"]): (
                r["value"],
                r["source"],
            )
            for r in _decode(scanned)
        }

    def append(self, observations: Iterable[Observation]) -> int:
        """Store new versions in one Iceberg commit; see ``AsOfStore.append``."""
        batch: dict[ObservationKey, Observation] = {}
        for observation in observations:
            if not isinstance(observation, Observation):
                raise TypeError(f"expected an Observation, got {type(observation).__name__}")
            earlier = batch.get(observation.key)
            if earlier is None:
                batch[observation.key] = observation
            elif (earlier.value, earlier.source) != (observation.value, observation.source):
                raise ConflictError(
                    f"version {observation.key} appears twice with a different value or source"
                )
        if not batch:
            return 0

        stored = self._stored(batch.values())
        new: list[Observation] = []
        for key, observation in batch.items():
            existing = stored.get(key)
            if existing is None:
                new.append(observation)
            elif existing != (observation.value, observation.source):
                raise ConflictError(
                    f"version {key} is already stored with a different value or source"
                )
        if not new:
            return 0

        rows = [
            {
                "series_id": o.series_id,
                "event_time": o.event_time,
                "knowledge_time": o.knowledge_time,
                "value": o.value,
                "source": o.source,
                "ingest_time": o.ingest_time,
            }
            for o in new
        ]
        self._table.append(pa.Table.from_pylist(rows, schema=self._arrow_schema))
        return len(new)
