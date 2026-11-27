"""A small revised series and experiment configuration for the experiment tests.

The series repeats one 52-week pattern. Every week is first published 10% too low (five days
after it starts) and revised to its true value a week later. Origins sit one day after a first
release, when the previous week has already been revised.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from series_fixture import START, UTC, seasonal_values
from vintage_lakehouse.experiment.runner import ExperimentConfig
from vintage_lakehouse.storage import InMemoryStore, Observation

SERIES = "periodic"
N_WEEKS = 160


def at(week: int, extra_days: int = 0) -> datetime:
    return datetime.combine(START + timedelta(weeks=week, days=extra_days), time.min, tzinfo=UTC)


def revised_observations(n_weeks: int = N_WEEKS, series: str = SERIES) -> list[Observation]:
    observations: list[Observation] = []
    for week, value in enumerate(seasonal_values(n_weeks)):
        event = START + timedelta(weeks=week)
        observations.append(Observation(series, event, at(week, 5), round(value * 0.9, 6), "test"))
        observations.append(Observation(series, event, at(week, 12), value, "test"))
    return observations


def revised_store() -> InMemoryStore:
    store = InMemoryStore()
    store.append(revised_observations())
    return store


def small_config(**overrides: object) -> ExperimentConfig:
    """Origins at weeks 130, 133, 136, 139; T_eval after the data; one exclusion window."""
    fields: dict[str, object] = {
        "series_id": SERIES,
        "models": ("seasonal_naive",),
        "first_origin": at(130, 6),
        "last_origin": at(139, 6),
        "origin_step_weeks": 3,
        "horizons": (1, 2),
        "evaluation_time": at(162),
        "sensitivity_weeks": (4, 8),
        "exclusions": ((at(133), at(135)),),
        "n_boot": 30,
        "block_weeks": 6,
        "revision_window": 4,
    }
    fields.update(overrides)
    return ExperimentConfig(**fields)  # type: ignore[arg-type]
