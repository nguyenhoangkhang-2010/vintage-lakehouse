"""Estimating how much provisional values are typically revised, by their age.

The method is deliberately simple (ADR 0010). The *lag* of a value is how many periods before the
latest reported period it lies. For every lag below ``max_lag`` the correction is the median, over
periods that had already settled, of how far the value at that lag was from the settled value:

- ``ratio``: settled / provisional (multiplicative);
- ``additive``: settled - provisional.

Everything is estimated from what was known at the cutoff: a period counts as *settled* when it
lies at least ``settle`` periods before the latest reported period, and its settled value is the
one known at the cutoff. Applying the correction to the lag-``k`` value of a history gives an
estimate of what that value will turn into.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from vintage_lakehouse.backfill.vintages import VintageTable

_KINDS = ("ratio", "additive")


@dataclass(frozen=True)
class CorrectionConfig:
    """How the correction is estimated.

    ``reverse_lags`` is the placebo of ADR 0010: the factor estimated for lag ``k`` is applied to
    lag ``max_lag - 1 - k``.
    """

    kind: str = "ratio"
    max_lag: int = 4
    settle: int = 26
    min_samples: int = 30
    step_days: int = 7
    reverse_lags: bool = False
    floor: float | None = 0.0

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"kind must be one of {_KINDS}, got {self.kind!r}")
        for name in ("max_lag", "settle", "min_samples", "step_days"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")


@dataclass(frozen=True)
class CorrectionFactors:
    """The estimated correction per lag, and how many pairs each estimate rests on.

    ``adjustment`` holds a factor (ratio) or an offset (additive) for every lag below
    ``max_lag``; lags with too few pairs hold the identity (1.0 or 0.0).
    """

    kind: str
    max_lag: int
    adjustment: dict[int, float]
    samples: dict[int, int]
    reverse_lags: bool = False
    floor: float | None = field(default=0.0)

    def apply(self, value: float, lag: int) -> float:
        """The corrected value of a provisional ``value`` that is ``lag`` periods old."""
        if lag < 0 or lag >= self.max_lag:
            return value
        key = self.max_lag - 1 - lag if self.reverse_lags else lag
        adjustment = self.adjustment[key]
        if self.kind == "ratio":
            return value * adjustment
        corrected = value + adjustment
        return corrected if self.floor is None else max(corrected, self.floor)


def estimate_correction(
    table: VintageTable, cutoff: datetime, config: CorrectionConfig
) -> CorrectionFactors:
    """Estimate the correction from what was known at ``cutoff``.

    A pair for lag ``k`` is a settled period ``p`` with its value when the latest reported
    period was ``p + k`` (the clock is the first release of ``p + k``) and its value at the
    cutoff. Nothing published after ``cutoff`` is read.
    """
    periods = table.periods_known(cutoff)
    pairs: dict[int, list[float]] = {lag: [] for lag in range(config.max_lag)}
    if periods:
        step = timedelta(days=config.step_days)
        settled_limit = periods[-1] - config.settle * step
        for period in periods:
            if period > settled_limit:
                break
            settled = table.value_at(period, cutoff)
            if settled is None:  # pragma: no cover - a period known at the cutoff has a value
                continue
            for lag in range(config.max_lag):
                clock = table.first_release(period + lag * step)
                if clock is None or clock > cutoff:
                    continue
                provisional = table.value_at(period, clock)
                if provisional is None:
                    continue
                if config.kind == "ratio":
                    if provisional > 0.0:
                        pairs[lag].append(settled / provisional)
                else:
                    pairs[lag].append(settled - provisional)

    identity = 1.0 if config.kind == "ratio" else 0.0
    adjustment = {
        lag: statistics.median(values) if len(values) >= config.min_samples else identity
        for lag, values in pairs.items()
    }
    return CorrectionFactors(
        kind=config.kind,
        max_lag=config.max_lag,
        adjustment=adjustment,
        samples={lag: len(values) for lag, values in pairs.items()},
        reverse_lags=config.reverse_lags,
        floor=config.floor,
    )
