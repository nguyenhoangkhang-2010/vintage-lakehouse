"""MMWR epidemiological week helpers.

MMWR weeks start on Sunday, and week 1 of a year is the week that contains January 4.
"""

from __future__ import annotations

from datetime import date, timedelta


def _week1_start(year: int) -> date:
    jan4 = date(year, 1, 4)
    return jan4 - timedelta(days=(jan4.weekday() + 1) % 7)


def weeks_in_year(year: int) -> int:
    """Return how many MMWR weeks ``year`` has (52 or 53)."""
    return (_week1_start(year + 1) - _week1_start(year)).days // 7


def date_to_epiweek(day: date) -> int:
    """Return the epiweek (for example ``202301``) that contains ``day``."""
    year = day.year
    if day >= _week1_start(year + 1):
        year += 1
    elif day < _week1_start(year):
        year -= 1
    week = (day - _week1_start(year)).days // 7 + 1
    return year * 100 + week


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
