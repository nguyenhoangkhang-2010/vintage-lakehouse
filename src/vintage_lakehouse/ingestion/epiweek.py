"""MMWR epidemiological week helpers.

MMWR weeks start on Sunday, and week 1 of a year is the week that contains January 4.
"""

from __future__ import annotations

from datetime import date, timedelta


def _week1_start(year: int) -> date:
    jan4 = date(year, 1, 4)
    return jan4 - timedelta(days=(jan4.weekday() + 1) % 7)


def epiweek_to_date(epiweek: int) -> date:
    """Return the Sunday that starts ``epiweek`` (an integer such as ``202301``).

    Raises ``ValueError`` if the week does not exist (for example week 53 in a 52-week year).
    """
    year, week = divmod(epiweek, 100)
    if not 1 <= week <= 53:
        raise ValueError(f"invalid epiweek: {epiweek}")
    start = _week1_start(year) + timedelta(weeks=week - 1)
    if start >= _week1_start(year + 1):
        raise ValueError(f"epiweek does not exist: {epiweek}")
    return start
