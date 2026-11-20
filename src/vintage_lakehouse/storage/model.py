"""Bitemporal observation model.

Implements the time model of ``docs/adr/0002-bitemporal-time-model.md``: every observation
carries the period it describes (``event_time``), the moment its source made this version
available (``knowledge_time``), and, optionally, when this project loaded it (``ingest_time``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import NamedTuple

UTC = timezone.utc


class ObservationKey(NamedTuple):
    """Identifies one version of one value: a new knowledge_time is a new version."""

    series_id: str
    event_time: date
    knowledge_time: datetime


def to_utc(moment: datetime, field: str) -> datetime:
    """Return ``moment`` converted to UTC.

    Raises ``TypeError`` if ``moment`` is not a datetime and ``ValueError`` if it is naive,
    because a naive timestamp does not identify an instant.
    """
    if not isinstance(moment, datetime):
        raise TypeError(f"{field} must be a datetime, not {type(moment).__name__}")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return moment.astimezone(UTC)


def knowledge_time_from_date(published: date) -> datetime:
    """Convert a publication *date* into a conservative knowledge_time.

    Some sources only state the day a version was released. A value released on day ``D`` may
    have been published at any moment of that day, so it is treated as known from the start of
    ``D + 1`` (00:00 UTC). This can only delay when a value counts as known, never advance it,
    so ``as_of`` queries cannot leak a value that was not yet available.
    """
    if isinstance(published, datetime) or not isinstance(published, date):
        raise TypeError(f"published must be a date, not {type(published).__name__}")
    return datetime.combine(published + timedelta(days=1), time.min, tzinfo=UTC)


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string, not {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


@dataclass(frozen=True, slots=True)
class Observation:
    """One published version of one value.

    ``knowledge_time`` and ``ingest_time`` are normalised to UTC. ``event_time`` is a plain
    date naming the period the value describes (for example the Sunday that starts an
    epidemiological week).

    Validation:

    - ``series_id`` and ``source`` are non-empty strings.
    - ``event_time`` is a ``date`` and not a ``datetime``.
    - ``knowledge_time`` is timezone-aware, and its UTC date is not before ``event_time``
      (a version cannot be known before the period it describes begins).
    - ``value`` is a finite real number (``bool``, NaN and infinities are rejected).
    - ``ingest_time`` is ``None`` or timezone-aware.
    """

    series_id: str
    event_time: date
    knowledge_time: datetime
    value: float
    source: str
    ingest_time: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.series_id, "series_id")
        _require_text(self.source, "source")

        if isinstance(self.event_time, datetime) or not isinstance(self.event_time, date):
            raise TypeError(f"event_time must be a date, not {type(self.event_time).__name__}")

        knowledge_time = to_utc(self.knowledge_time, "knowledge_time")
        if knowledge_time.date() < self.event_time:
            raise ValueError(
                f"knowledge_time {knowledge_time.isoformat()} is before the period it "
                f"describes (event_time {self.event_time.isoformat()})"
            )
        object.__setattr__(self, "knowledge_time", knowledge_time)

        if isinstance(self.value, bool) or not isinstance(self.value, int | float):
            raise TypeError(f"value must be a real number, not {type(self.value).__name__}")
        number = float(self.value)
        if not math.isfinite(number):
            raise ValueError(f"value must be finite, got {self.value!r}")
        object.__setattr__(self, "value", number)

        if self.ingest_time is not None:
            object.__setattr__(self, "ingest_time", to_utc(self.ingest_time, "ingest_time"))

    @property
    def key(self) -> ObservationKey:
        """The identity of this version."""
        return ObservationKey(self.series_id, self.event_time, self.knowledge_time)
