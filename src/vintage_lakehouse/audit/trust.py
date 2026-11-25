"""Dataset trust report: how a series' values get revised, from its stored vintages.

The report describes facts about revision behaviour (ADR 0011), computed only from versions known
at a chosen moment. A period is *settled* when it lies at least ``settle`` periods before the
latest reported period, and its settled value is the one known at that moment. The *lag* of a
value is how many periods before the latest reported period it was when it was current (0 for a
first release), as in ``vintage_lakehouse.backfill``.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any

from vintage_lakehouse.backfill import VintageTable
from vintage_lakehouse.forecast.series import empirical_quantile
from vintage_lakehouse.storage.model import to_utc

_BIAS_THRESHOLD = 0.02  # median lag-0 ratio further than this from 1 is called biased
_DRIFT_THRESHOLD = 0.05  # yearly medians spanning more than this are called drifting
_SLOW_LAG = 4  # a 90th percentile settling lag above this is called slow


@dataclass(frozen=True)
class TrustConfig:
    """Settings of the report. The defaults are those of ADR 0011."""

    max_lag: int = 7
    settle: int = 26
    tolerance: float = 0.01
    step_days: int = 7
    min_year_samples: int = 20

    def __post_init__(self) -> None:
        if self.max_lag < 0:
            raise ValueError("max_lag must not be negative")
        for name in ("settle", "step_days", "min_year_samples"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.tolerance < 0.0:
            raise ValueError("tolerance must not be negative")


@dataclass(frozen=True)
class LagStats:
    """How values of one age differ from their settled value."""

    lag: int
    n: int
    share_changed: float | None
    share_material: float | None
    median_ratio: float | None
    mean_abs_rel: float | None
    p90_abs_rel: float | None


@dataclass(frozen=True)
class YearStats:
    """Lag-0 revision of the settled periods of one calendar year."""

    year: int
    n: int
    median_ratio: float | None
    mean_abs_rel: float | None


@dataclass(frozen=True)
class TrustReport:
    """What is known about the revision behaviour of a series."""

    series_id: str
    as_of: datetime | None
    config: TrustConfig
    n_periods: int
    n_versions: int
    first_period: date | None
    last_period: date | None
    missing_periods: int
    n_settled: int
    n_unsettled: int
    lags: list[LagStats]
    settled_within: dict[int, float]
    settled_lag_median: float | None
    settled_lag_p90: float | None
    days_to_final_median: float | None
    days_to_final_p90: float | None
    years: list[YearStats]
    findings: list[str]


def _quantile(values: list[float], level: float) -> float | None:
    return empirical_quantile(sorted(values), level) if values else None


def _mean(values: list[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _within(value: float, settled: float, tolerance: float) -> bool:
    return abs(value - settled) <= tolerance * abs(settled) if settled else abs(value) <= tolerance


def build_trust_report(
    table: VintageTable,
    series_id: str,
    *,
    as_of: datetime | None = None,
    config: TrustConfig | None = None,
) -> TrustReport:
    """Describe the revision behaviour of a series as it was known at ``as_of``.

    ``as_of`` defaults to the latest knowledge_time in the table. Versions published after it
    are not used.
    """
    config = config or TrustConfig()
    moment = to_utc(as_of, "as_of") if as_of is not None else None
    if moment is None:
        latest_times = [table.versions(p)[-1][0] for p in table.periods() if table.versions(p)]
        moment = max(latest_times) if latest_times else None
    if moment is None:
        return _empty(series_id, config)

    periods = table.periods_known(moment)
    if not periods:
        return _empty(series_id, config, as_of=moment)

    step = timedelta(days=config.step_days)
    latest = periods[-1]
    n_versions = sum(1 for p in periods for t, _ in table.versions(p) if t <= moment)
    span = (latest - periods[0]).days // config.step_days + 1
    missing = max(0, span - len(periods))
    settled_limit = latest - config.settle * step
    settled = [p for p in periods if p <= settled_limit]

    pairs: dict[int, list[tuple[float, float]]] = {lag: [] for lag in range(config.max_lag + 1)}
    settled_lags: list[int] = []
    days_to_final: list[float] = []
    yearly: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for period in settled:
        final = table.value_at(period, moment)
        if final is None:  # pragma: no cover - a period known at the moment has a value
            continue
        values: dict[int, float] = {}
        for lag in range(config.settle + 1):
            clock = table.first_release(period + lag * step)
            if clock is None or clock > moment:
                continue
            value = table.value_at(period, clock)
            if value is not None:
                values[lag] = value
        for lag in range(config.max_lag + 1):
            if lag in values:
                pairs[lag].append((values[lag], final))
        if 0 in values and values[0] > 0.0 and final != 0.0:
            yearly[period.year].append((values[0], final))

        settled_lag = config.settle + 1
        for lag in sorted(values, reverse=True):
            if not _within(values[lag], final, config.tolerance):
                break
            settled_lag = lag
        settled_lags.append(settled_lag)

        known_versions = [t for t, _ in table.versions(period) if t <= moment]
        days_to_final.append((known_versions[-1] - known_versions[0]).total_seconds() / 86400.0)

    lags = [_lag_stats(lag, pairs[lag], config) for lag in range(config.max_lag + 1)]
    settled_within = {
        lag: (sum(1 for s in settled_lags if s <= lag) / len(settled_lags) if settled_lags else 0.0)
        for lag in range(config.max_lag + 1)
    }
    years = [
        YearStats(
            year=year,
            n=len(items),
            median_ratio=statistics.median(f / v for v, f in items),
            mean_abs_rel=_mean([abs(f - v) / abs(f) for v, f in items]),
        )
        for year, items in sorted(yearly.items())
    ]
    lag_values = [float(s) for s in settled_lags]
    report = TrustReport(
        series_id=series_id,
        as_of=moment,
        config=config,
        n_periods=len(periods),
        n_versions=n_versions,
        first_period=periods[0],
        last_period=latest,
        missing_periods=missing,
        n_settled=len(settled),
        n_unsettled=len(periods) - len(settled),
        lags=lags,
        settled_within=settled_within,
        settled_lag_median=_quantile(lag_values, 0.5),
        settled_lag_p90=_quantile(lag_values, 0.9),
        days_to_final_median=_quantile(days_to_final, 0.5),
        days_to_final_p90=_quantile(days_to_final, 0.9),
        years=years,
        findings=[],
    )
    return _with_findings(report)


def _lag_stats(lag: int, items: list[tuple[float, float]], config: TrustConfig) -> LagStats:
    if not items:
        return LagStats(lag, 0, None, None, None, None, None)
    changed = sum(1 for v, f in items if abs(f - v) > 1e-12 * max(1.0, abs(f)))
    material = sum(1 for v, f in items if not _within(v, f, config.tolerance))
    ratios = [f / v for v, f in items if v > 0.0]
    relative = [abs(f - v) / abs(f) for v, f in items if f != 0.0]
    return LagStats(
        lag=lag,
        n=len(items),
        share_changed=changed / len(items),
        share_material=material / len(items),
        median_ratio=statistics.median(ratios) if ratios else None,
        mean_abs_rel=_mean(relative),
        p90_abs_rel=_quantile(relative, 0.9),
    )


def _empty(series_id: str, config: TrustConfig, as_of: datetime | None = None) -> TrustReport:
    return TrustReport(
        series_id=series_id,
        as_of=as_of,
        config=config,
        n_periods=0,
        n_versions=0,
        first_period=None,
        last_period=None,
        missing_periods=0,
        n_settled=0,
        n_unsettled=0,
        lags=[],
        settled_within={},
        settled_lag_median=None,
        settled_lag_p90=None,
        days_to_final_median=None,
        days_to_final_p90=None,
        years=[],
        findings=["No data: the series has no values."],
    )


def _with_findings(report: TrustReport) -> TrustReport:
    findings: list[str] = []
    if report.n_settled == 0:
        findings.append(
            f"No period is settled yet (a period needs {report.config.settle} later periods), "
            "so revisions cannot be described."
        )
    else:
        lag0 = report.lags[0]
        if lag0.median_ratio is not None and abs(lag0.median_ratio - 1.0) > _BIAS_THRESHOLD:
            direction = "higher" if lag0.median_ratio > 1.0 else "lower"
            findings.append(
                f"First release is biased: the median settled value is "
                f"{abs(lag0.median_ratio - 1.0) * 100:.1f}% {direction} than the first release."
            )
        eligible = [
            y
            for y in report.years
            if y.median_ratio is not None and y.n >= report.config.min_year_samples
        ]
        if len(eligible) >= 2:
            low = min(eligible, key=lambda y: y.median_ratio or 0.0)
            high = max(eligible, key=lambda y: y.median_ratio or 0.0)
            if (high.median_ratio or 0.0) - (low.median_ratio or 0.0) > _DRIFT_THRESHOLD:
                findings.append(
                    f"The bias drifts: the yearly median lag-0 ratio ranges from "
                    f"{low.median_ratio:.3f} ({low.year}) to {high.median_ratio:.3f} "
                    f"({high.year}), so an estimate from the past may not hold now."
                )
        if report.settled_lag_p90 is not None and report.settled_lag_p90 > _SLOW_LAG:
            findings.append(
                f"Slow to settle: 90% of periods are final within "
                f"{_lag_text(report.settled_lag_p90, report.config.settle)} of their first "
                f"release."
            )
    if report.missing_periods:
        findings.append(
            f"{report.missing_periods} missing period(s) between "
            f"{report.first_period} and {report.last_period}."
        )
    findings.append(
        f"The latest {report.n_unsettled} periods are not yet settled: treat them as provisional."
    )
    return replace(report, findings=findings)


def trust_report_to_dict(report: TrustReport) -> dict[str, Any]:
    """A JSON-ready dictionary of the report."""

    def convert(value: Any) -> Any:
        if isinstance(value, datetime | date):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): convert(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [convert(v) for v in value]
        return value

    return {key: convert(value) for key, value in asdict(report).items()}


def _lag_text(lag: float | None, settle: int) -> str:
    """A settling lag in words. The value ``settle + 1`` means "not within the window"."""
    if lag is None:
        return "n/a"
    if lag > settle:
        return f"more than {settle} periods"
    return f"{lag:.0f} period" + ("" if lag == 1 else "s")


def _fmt(value: float | None, spec: str = "{:.3f}") -> str:
    return "n/a" if value is None else spec.format(value)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def render_trust_markdown(report: TrustReport) -> str:
    """The report as Markdown."""
    parts = [f"# Trust report: {report.series_id}", ""]
    if report.n_periods == 0:
        parts += [*report.findings, ""]
        return "\n".join(parts)
    config = report.config
    parts += [
        f"As of {report.as_of:%Y-%m-%d %H:%M} UTC. {report.n_periods} periods "
        f"({report.first_period} to {report.last_period}), {report.n_versions} versions, "
        f"{report.n_settled} settled (at least {config.settle} periods old) and "
        f"{report.n_unsettled} not yet.",
        "",
        "## Findings",
        "",
        *[f"- {finding}" for finding in report.findings],
        "",
        "## Revision by age",
        "",
        "Lag 0 is the latest reported period. Ratio is settled value / value at that age; "
        f"material means more than {config.tolerance:.0%} away from the settled value.",
        "",
        _table(
            ["Lag", "Periods", "Changed", "Material", "Median ratio", "Mean |rel|", "P90 |rel|"],
            [
                [
                    str(s.lag),
                    str(s.n),
                    _pct(s.share_changed),
                    _pct(s.share_material),
                    _fmt(s.median_ratio),
                    _pct(s.mean_abs_rel),
                    _pct(s.p90_abs_rel),
                ]
                for s in report.lags
            ],
        ),
        "",
        "## Settling",
        "",
        f"Lag from which a value stays within {config.tolerance:.0%} of its final value: "
        f"median {_lag_text(report.settled_lag_median, config.settle)}, 90th percentile "
        f"{_lag_text(report.settled_lag_p90, config.settle)}. Days from first release to last "
        f"revision: "
        f"median {_fmt(report.days_to_final_median, '{:.0f}')}, 90th percentile "
        f"{_fmt(report.days_to_final_p90, '{:.0f}')}.",
        "",
        _table(
            ["Final within lag", "Share of periods"],
            [[str(lag), _pct(share)] for lag, share in report.settled_within.items()],
        ),
        "",
        "## Bias by year",
        "",
        "Median ratio of settled to first-release value, by calendar year of the period.",
        "",
        _table(
            ["Year", "Periods", "Median ratio", "Mean |rel|"],
            [
                [str(y.year), str(y.n), _fmt(y.median_ratio), _pct(y.mean_abs_rel)]
                for y in report.years
            ],
        ),
        "",
    ]
    return "\n".join(parts)
