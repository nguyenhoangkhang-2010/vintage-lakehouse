"""A pipeline that only uses what was known at its origin: `audit leak-check` finds no leak."""

from __future__ import annotations

from datetime import datetime
from statistics import fmean

from vintage_lakehouse.storage import AsOfStore


def pipeline(store: AsOfStore, series_id: str, origin: datetime) -> float:
    """Forecast the next value as the mean of the last four values known at the origin."""
    known = store.as_of(series_id, origin)
    return fmean(o.value for o in known[-4:])
