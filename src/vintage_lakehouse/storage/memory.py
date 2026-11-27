"""In-memory reference implementation of the ``AsOfStore`` contract.

It is the executable specification: simple enough to read in one sitting, and the baseline
that the Iceberg-backed store is compared against. It is not meant for large data.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from datetime import date, datetime

from vintage_lakehouse.storage.base import ConflictError
from vintage_lakehouse.storage.model import Observation, ObservationKey, to_utc


def _known_at(observation: Observation) -> datetime:
    return observation.knowledge_time


class InMemoryStore:
    """Keeps every version of every observation in memory.

    Versions of one (series, event_time) are held in a list sorted by knowledge_time, so
    ``as_of`` is a binary search per event_time.
    """

    def __init__(self) -> None:
        self._versions: dict[str, dict[date, list[Observation]]] = {}

    def _find(self, key: ObservationKey) -> Observation | None:
        versions = self._versions.get(key.series_id, {}).get(key.event_time, [])
        index = bisect.bisect_left(versions, key.knowledge_time, key=_known_at)
        if index < len(versions) and versions[index].knowledge_time == key.knowledge_time:
            return versions[index]
        return None

    def append(self, observations: Iterable[Observation]) -> int:
        """Store new versions atomically; see ``AsOfStore.append``."""
        staged: dict[ObservationKey, Observation] = {}
        for observation in observations:
            if not isinstance(observation, Observation):
                raise TypeError(f"expected an Observation, got {type(observation).__name__}")
            existing = self._find(observation.key) or staged.get(observation.key)
            if existing is None:
                staged[observation.key] = observation
            elif existing.value != observation.value or existing.source != observation.source:
                raise ConflictError(
                    f"version {observation.key} is already stored with a different value or source"
                )

        for observation in staged.values():
            by_event = self._versions.setdefault(observation.series_id, {})
            bisect.insort(
                by_event.setdefault(observation.event_time, []), observation, key=_known_at
            )
        return len(staged)

    def as_of(self, series_id: str, at: datetime) -> list[Observation]:
        """Return the latest version known at ``at`` for each event_time."""
        moment = to_utc(at, "at")
        by_event = self._versions.get(series_id, {})
        result: list[Observation] = []
        for event_time in sorted(by_event):
            versions = by_event[event_time]
            index = bisect.bisect_right(versions, moment, key=_known_at)
            if index:
                result.append(versions[index - 1])
        return result

    def revisions(self, series_id: str, event_time: date) -> list[Observation]:
        """Return all versions of one event_time, oldest first."""
        return list(self._versions.get(series_id, {}).get(event_time, []))
