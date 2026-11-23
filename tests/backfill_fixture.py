"""A weekly series whose values are revised by a known amount at each report.

Report ``i`` is published five days after week ``i`` starts and contains every week up to ``i``.
Week ``w`` therefore goes through reports ``w``, ``w + 1``, ... and at report ``w + k`` (its lag
``k``) its value is ``truth[w] * factors[k]``. After the last factor the value no longer changes.
With the default factors (0.8, 0.9, 1.0) a first release is 20% too low, the second 10% too low,
and the third is final. The settled-over-provisional ratio is then exactly 1.25 at lag 0 and
1 / 0.9 at lag 1, which makes the correction easy to check by hand.

Only reports that exist are generated: week ``w`` has a version at lag ``k`` only if report
``w + k`` is within the series.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta, timezone

from vintage_lakehouse.storage import InMemoryStore, Observation

UTC = timezone.utc
BASE = date(2023, 1, 1)  # a Sunday
SERIES = "rev"
FACTORS = (0.8, 0.9, 1.0)


def week(n: int) -> date:
    return BASE + timedelta(weeks=n)


def report_time(n: int) -> datetime:
    """When report ``n`` (the one that first contains week ``n``) is published."""
    return datetime.combine(week(n) + timedelta(days=5), time.min, tzinfo=UTC)


def origin(n: int) -> datetime:
    """Just after report ``n`` is published."""
    return datetime.combine(week(n) + timedelta(days=6), time.min, tzinfo=UTC)


def revised_series(
    n_weeks: int,
    truth: Sequence[float] | Callable[[int], float],
    *,
    factors: Sequence[float] | Callable[[int], Sequence[float]] = FACTORS,
    series: str = SERIES,
) -> list[Observation]:
    """Every published version of ``n_weeks`` weeks (see the module docstring)."""
    out: list[Observation] = []
    for w in range(n_weeks):
        true_value = truth(w) if callable(truth) else truth[w]
        week_factors = factors(w) if callable(factors) else factors
        for k, factor in enumerate(week_factors):
            if w + k >= n_weeks:
                break
            out.append(
                Observation(series, week(w), report_time(w + k), true_value * factor, "test")
            )
    return out


def make_store(observations: Sequence[Observation]) -> InMemoryStore:
    store = InMemoryStore()
    store.append(observations)
    return store


def varying_truth(w: int) -> float:
    """A positive series that is not constant, so that additive revisions differ by week."""
    return 5.0 + (w % 7) * 0.5
