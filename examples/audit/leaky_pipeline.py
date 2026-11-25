"""A pipeline with a seeded leak: it normalises by the settled values of today's snapshot.

Looking up the history "as of now" instead of "as of the origin" gives every past period its
revised value, which a forecaster at the origin did not have. Nothing fails and the numbers look
fine, which is why `audit leak-check` exists. `audit lint` does not catch this one, because the
time passes through a variable: it is a heuristic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import fmean

from vintage_lakehouse.storage import AsOfStore

TODAY = datetime(2200, 1, 1, tzinfo=timezone.utc)


def pipeline(store: AsOfStore, series_id: str, origin: datetime) -> float:
    """Forecast the next value as the mean of the last four values, read from a snapshot."""
    known = store.as_of(series_id, origin)
    snapshot = {o.event_time: o.value for o in store.as_of(series_id, TODAY)}
    return fmean(snapshot[o.event_time] for o in known[-4:])
