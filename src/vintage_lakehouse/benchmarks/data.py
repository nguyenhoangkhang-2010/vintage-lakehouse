"""Deterministic synthetic data for the benchmarks.

Every series is weekly. Week ``p`` is first published at report ``p`` (five days after the week
starts) and then revised at each following weekly report, for ``versions_per_period`` versions in
all. Only reports that exist are generated, so the last weeks have fewer versions, exactly as in a
real, still-running source. Values are random doubles, which compress poorly (ADR 0013).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from vintage_lakehouse.storage.model import Observation

UTC = timezone.utc
BASE = date(2000, 1, 2)  # a Sunday


@dataclass(frozen=True)
class SyntheticSpec:
    """The shape of a synthetic table."""

    n_series: int = 1
    n_periods: int = 400
    versions_per_period: int = 20
    seed: int = 0

    def __post_init__(self) -> None:
        for name in ("n_series", "n_periods", "versions_per_period"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")

    def series_ids(self) -> list[str]:
        """The ids of the series, in order."""
        return [f"bench/series-{i:04d}" for i in range(self.n_series)]

    def expected_versions(self) -> int:
        """How many versions ``generate`` produces."""
        n, v = self.n_periods, self.versions_per_period
        per_series = n * (n + 1) // 2 if n < v else v * (n - v + 1) + v * (v - 1) // 2
        return self.n_series * per_series


def report_time(report: int) -> datetime:
    """When report number ``report`` is published."""
    day = BASE + timedelta(weeks=report, days=5)
    return datetime.combine(day, time.min, tzinfo=UTC)


def generate_series(spec: SyntheticSpec, index: int) -> list[Observation]:
    """Every version of one series of the table described by ``spec``."""
    rng = random.Random(spec.seed * 1_000_003 + index)
    series_id = spec.series_ids()[index]
    out: list[Observation] = []
    for period in range(spec.n_periods):
        truth = 3.0 + 2.0 * math.sin(2.0 * math.pi * period / 52.0) + rng.random()
        for k in range(min(spec.versions_per_period, spec.n_periods - period)):
            value = truth * (1.0 + rng.uniform(-0.2, 0.2) / (k + 1))
            out.append(
                Observation(
                    series_id=series_id,
                    event_time=BASE + timedelta(weeks=period),
                    knowledge_time=report_time(period + k),
                    value=value,
                    source="benchmark",
                )
            )
    return out


def generate(spec: SyntheticSpec) -> list[Observation]:
    """Every version of every series."""
    return [o for index in range(spec.n_series) for o in generate_series(spec, index)]
