"""Forecast scoring: weighted interval score (WIS), absolute error and interval coverage.

A probabilistic forecast is a mapping from quantile level to value, for example
``{0.25: 8.5, 0.5: 9.0, 0.75: 9.5}``. The levels must contain the median (0.5) and be symmetric
around it, so that they form central prediction intervals.

WIS follows Bracher et al. (2021). With K central intervals at levels ``alpha_k`` (so
``alpha_k / 2`` and ``1 - alpha_k / 2`` are the quantile levels), the median ``m`` and the
observed value ``y``::

    IS_alpha(l, u, y) = (u - l) + (2 / alpha) * max(l - y, 0) + (2 / alpha) * max(y - u, 0)
    WIS = (0.5 * |y - m| + sum_k (alpha_k / 2) * IS_alpha_k) / (K + 0.5)

Lower is better. With only the median (K = 0) WIS equals the absolute error.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping

MEDIAN = 0.5
_PRECISION = 9  # decimals used to compare quantile levels such as 0.025 and 1 - 0.975


def normalise_quantiles(quantiles: Mapping[float, float]) -> list[tuple[float, float]]:
    """Validate a quantile forecast and return it as ``(level, value)`` pairs, ascending.

    Raises ``ValueError`` if a level is outside (0, 1), appears twice, has no symmetric partner,
    if the median is missing, if a value is not finite, or if quantiles cross.
    """
    levels: dict[float, float] = {}
    for level, value in quantiles.items():
        if not 0.0 < level < 1.0:
            raise ValueError(f"quantile level must be in (0, 1), got {level!r}")
        if not math.isfinite(value):
            raise ValueError(f"quantile value must be finite, got {value!r} at level {level}")
        key = round(level, _PRECISION)
        if key in levels:
            raise ValueError(f"quantile level {level} appears twice")
        levels[key] = float(value)

    if MEDIAN not in levels:
        raise ValueError("the median (level 0.5) is required")
    for level in levels:
        partner = round(1.0 - level, _PRECISION)
        if partner not in levels:
            raise ValueError(f"quantile level {level} has no symmetric partner {partner}")

    ordered = sorted(levels.items())
    for (low_level, low), (high_level, high) in itertools.pairwise(ordered):
        if high < low:
            raise ValueError(
                f"quantiles must not cross: {low} at level {low_level} "
                f"is above {high} at level {high_level}"
            )
    return ordered


def _intervals(ordered: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """Return ``(alpha, lower, upper)`` for every central interval."""
    values = dict(ordered)
    return [
        (2.0 * level, value, values[round(1.0 - level, _PRECISION)])
        for level, value in ordered
        if level < MEDIAN
    ]


def _check_observed(observed: float) -> None:
    if not math.isfinite(observed):
        raise ValueError(f"the observed value must be finite, got {observed!r}")


def weighted_interval_score(quantiles: Mapping[float, float], observed: float) -> float:
    """Return the WIS of a quantile forecast for one observed value."""
    _check_observed(observed)
    ordered = normalise_quantiles(quantiles)
    median = dict(ordered)[MEDIAN]
    intervals = _intervals(ordered)
    total = 0.5 * abs(observed - median)
    for alpha, lower, upper in intervals:
        score = (
            (upper - lower)
            + (2.0 / alpha) * max(lower - observed, 0.0)
            + (2.0 / alpha) * max(observed - upper, 0.0)
        )
        total += (alpha / 2.0) * score
    return total / (len(intervals) + 0.5)


def absolute_error(quantiles: Mapping[float, float], observed: float) -> float:
    """Return ``|median - observed|``, the point-forecast error."""
    _check_observed(observed)
    return abs(dict(normalise_quantiles(quantiles))[MEDIAN] - observed)


def central_coverage(quantiles: Mapping[float, float], observed: float) -> dict[float, bool]:
    """Return, for each central interval, whether it contains the observed value.

    The keys are the nominal coverage levels, for example ``0.5`` for the interval between the
    0.25 and 0.75 quantiles.
    """
    _check_observed(observed)
    return {
        round(1.0 - alpha, _PRECISION): lower <= observed <= upper
        for alpha, lower, upper in _intervals(normalise_quantiles(quantiles))
    }
