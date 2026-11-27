"""The contract every ``as_of`` store must satisfy.

The contract is tested by ``tests/store_contract.py``. Every implementation (the in-memory
reference store, and later the Iceberg-backed store) must pass the same tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from vintage_lakehouse.storage.model import Observation


class ConflictError(ValueError):
    """A version with the same key was already stored with a different value or source."""


@runtime_checkable
class AsOfStore(Protocol):
    """A store of observation versions that can answer "what was known at time t?".

    Rules shared by all implementations:

    - A version is identified by its key (series_id, event_time, knowledge_time).
    - Appending a version that is already stored with the same value and source is a no-op.
      ``ingest_time`` is informational and is ignored for this comparison.
    - Appending a version whose key exists with a different value or source raises
      ``ConflictError``.
    - ``append`` is atomic: if any item in the batch is invalid or conflicts, nothing from the
      batch is stored.
    - ``as_of`` never returns a version with ``knowledge_time`` later than the requested time.
    """

    def append(self, observations: Iterable[Observation]) -> int:
        """Store new versions and return how many were newly stored.

        Duplicates (see the rules above) are not counted. Raises ``TypeError`` if an item is
        not an ``Observation`` and ``ConflictError`` on a conflicting version.
        """
        ...

    def as_of(self, series_id: str, at: datetime) -> list[Observation]:
        """Return what was knowable at ``at``.

        For every event_time of the series that has at least one version with
        ``knowledge_time <= at`` (the boundary is inclusive), return the version with the
        latest such knowledge_time. The result is ordered by event_time, ascending, and is a
        new list. An unknown series gives an empty list. ``at`` must be timezone-aware
        (``ValueError`` otherwise) and any offset is accepted.
        """
        ...

    def revisions(self, series_id: str, event_time: date) -> list[Observation]:
        """Return every stored version of one event_time, oldest knowledge_time first.

        The result is a new list and is empty if nothing is stored for that period.
        """
        ...
