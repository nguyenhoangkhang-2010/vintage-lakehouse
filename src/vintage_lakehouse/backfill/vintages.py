"""Every published version of every period, queried as of a moment.

A normal history holds one value per period, the latest known. To learn how provisional values
get revised, a model needs the earlier versions as well: what a period looked like when it was
first reported, one report later, and so on. ``VintageTable`` holds all versions of one series and
answers "what was the value of period ``p`` as of time ``t``?" without any look-ahead beyond what
the caller asks for.

The table is loaded once, up to some time, and queried many times. Loading more than a forecast
origin needs is safe only because every query takes the moment it is asked as of; the correction
in ``vintage_lakehouse.backfill.correction`` never asks about a later moment than its cutoff.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from datetime import date, datetime

from vintage_lakehouse.storage.base import AsOfStore
from vintage_lakehouse.storage.model import Observation, to_utc


class VintageTable:
    """All versions of the periods of one series, ordered by knowledge_time."""

    def __init__(self, versions: dict[date, list[Observation]]) -> None:
        self._times: dict[date, list[datetime]] = {}
        self._values: dict[date, list[float]] = {}
        for period, observations in versions.items():
            ordered = sorted(observations, key=lambda o: o.knowledge_time)
            self._times[period] = [o.knowledge_time for o in ordered]
            self._values[period] = [o.value for o in ordered]
        self._periods = sorted(versions)

    @classmethod
    def from_observations(cls, observations: Iterable[Observation]) -> VintageTable:
        """Build a table from versions of a single series."""
        by_period: dict[date, list[Observation]] = {}
        series: str | None = None
        for observation in observations:
            if series is None:
                series = observation.series_id
            elif observation.series_id != series:
                raise ValueError(
                    f"a vintage table holds one series, got {series!r} and "
                    f"{observation.series_id!r}"
                )
            by_period.setdefault(observation.event_time, []).append(observation)
        return cls(by_period)

    @classmethod
    def from_store(cls, store: AsOfStore, series_id: str, up_to: datetime) -> VintageTable:
        """Load every version of the series that was known at ``up_to`` or earlier."""
        limit = to_utc(up_to, "up_to")
        by_period: dict[date, list[Observation]] = {}
        for latest in store.as_of(series_id, limit):
            versions = [
                v
                for v in store.revisions(series_id, latest.event_time)
                if v.knowledge_time <= limit
            ]
            by_period[latest.event_time] = versions
        return cls(by_period)

    def first_release(self, period: date) -> datetime | None:
        """When the period was first reported, or ``None`` if it is not in the table."""
        times = self._times.get(period)
        return times[0] if times else None

    def value_at(self, period: date, moment: datetime) -> float | None:
        """The value of ``period`` as known at ``moment`` (inclusive), or ``None`` if not yet."""
        times = self._times.get(period)
        if not times:
            return None
        position = bisect_right(times, moment)
        return self._values[period][position - 1] if position else None

    def periods(self) -> list[date]:
        """Every period in the table, in order, whatever its first release."""
        return list(self._periods)

    def versions(self, period: date) -> list[tuple[datetime, float]]:
        """The versions of a period as (knowledge_time, value), oldest first (empty if unknown)."""
        return list(zip(self._times.get(period, []), self._values.get(period, []), strict=True))

    def periods_known(self, moment: datetime) -> list[date]:
        """The periods first reported at ``moment`` or earlier, in order."""
        return [p for p in self._periods if self._times[p][0] <= moment]
