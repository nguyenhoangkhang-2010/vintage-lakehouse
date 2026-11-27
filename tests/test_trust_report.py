"""Tests for the dataset trust report, hand-computed on the series of ``backfill_fixture``.

With factors (0.8, 0.9, 1.0) a first release is 20% below the settled value, the second report
10% below, and the third is final. Reports are a week apart, so a period is final 14 days after
its first release. At the default settling window of 26 periods and 130 weeks of data, weeks
0 to 103 are settled and 26 are not.
"""

from __future__ import annotations

import json

import pytest

from backfill_fixture import report_time, revised_series, varying_truth, week
from vintage_lakehouse.audit import (
    TrustConfig,
    TrustReport,
    build_trust_report,
    render_trust_markdown,
    trust_report_to_dict,
)
from vintage_lakehouse.backfill import VintageTable
from vintage_lakehouse.storage import Observation

N = 130


def report_for(
    observations: list[Observation],
    *,
    as_of: object = None,
    config: TrustConfig | None = None,
) -> TrustReport:
    table = VintageTable.from_observations(observations)
    return build_trust_report(table, "rev", as_of=as_of, config=config)  # type: ignore[arg-type]


def test_coverage_is_counted() -> None:
    r = report_for(revised_series(N, varying_truth))
    assert r.series_id == "rev"
    assert (r.n_periods, r.first_period, r.last_period) == (N, week(0), week(N - 1))
    assert r.n_versions == 3 * N - 3  # the last two weeks have fewer versions yet: 1 + 2
    assert (r.n_settled, r.n_unsettled, r.missing_periods) == (104, 26, 0)
    assert r.as_of == report_time(N - 1)


def test_revision_by_lag_is_hand_computed() -> None:
    r = report_for(revised_series(N, varying_truth))
    lag0, lag1, lag2 = r.lags[0], r.lags[1], r.lags[2]
    assert (lag0.lag, lag0.n) == (0, 104)
    assert lag0.share_changed == 1.0 and lag0.share_material == 1.0
    assert lag0.median_ratio == pytest.approx(1.25)
    assert lag0.mean_abs_rel == pytest.approx(0.2)
    assert lag0.p90_abs_rel == pytest.approx(0.2)
    assert lag1.median_ratio == pytest.approx(1 / 0.9)
    assert lag1.mean_abs_rel == pytest.approx(0.1)
    assert lag2.share_changed == 0.0 and lag2.share_material == 0.0
    assert lag2.median_ratio == pytest.approx(1.0)
    assert lag2.mean_abs_rel == 0.0
    assert [s.lag for s in r.lags] == list(range(8))  # max_lag 7 by default: lags 0..7


def test_settling_is_the_lag_from_which_values_stay_final() -> None:
    r = report_for(revised_series(N, varying_truth))
    assert r.settled_within[0] == 0.0 and r.settled_within[1] == 0.0
    assert r.settled_within[2] == 1.0 and r.settled_within[7] == 1.0
    assert (r.settled_lag_median, r.settled_lag_p90) == (2, 2)
    assert r.days_to_final_median == pytest.approx(14.0)
    assert r.days_to_final_p90 == pytest.approx(14.0)


def test_a_small_revision_is_changed_but_not_material() -> None:
    r = report_for(revised_series(N, [10.0] * N, factors=(0.995, 1.0)))
    assert r.lags[0].share_changed == 1.0
    assert r.lags[0].share_material == 0.0  # 0.5% is within the 1% tolerance
    assert r.settled_lag_median == 0  # already within tolerance at the first release


def test_bias_by_year_and_the_findings_for_a_constant_bias() -> None:
    r = report_for(revised_series(N, varying_truth))
    assert [y.year for y in r.years] == [2023, 2024]
    assert sum(y.n for y in r.years) == 104
    assert all(y.median_ratio == pytest.approx(1.25) for y in r.years)
    text = "\n".join(r.findings)
    assert "first release is biased" in text.lower() and "25.0%" in text
    assert "drift" not in text.lower()
    assert "slow" not in text.lower()
    assert "26 periods" in text  # the provisional tail is always stated


def test_a_drifting_bias_is_flagged() -> None:
    def factors(w: int) -> tuple[float, ...]:
        return (0.8, 1.0) if w < 60 else (1.0, 1.0)

    r = report_for(revised_series(N, [10.0] * N, factors=factors))
    by_year = {y.year: y.median_ratio for y in r.years}
    assert by_year[2023] == pytest.approx(1.25)
    assert by_year[2024] == pytest.approx(1.0)
    assert any("drift" in f.lower() and "2023" in f and "2024" in f for f in r.findings)


def test_years_with_few_periods_do_not_count_for_the_drift_rule() -> None:
    def factors(w: int) -> tuple[float, ...]:
        return (0.8, 1.0) if w < 60 else (1.0, 1.0)

    config = TrustConfig(min_year_samples=60)  # neither year has 60 settled periods
    r = report_for(revised_series(N, [10.0] * N, factors=factors), config=config)
    assert not any("drift" in f.lower() for f in r.findings)


def test_a_value_that_is_final_early_but_revised_later_has_not_settled() -> None:
    # right at the first release, off at the second report, right again from the third
    r = report_for(revised_series(N, [10.0] * N, factors=(1.0, 0.8, 1.0)))
    assert (r.settled_lag_median, r.settled_lag_p90) == (2, 2)
    assert r.settled_within[0] == 0.0 and r.settled_within[1] == 0.0
    assert r.settled_within[2] == 1.0


def test_a_difference_exactly_at_the_tolerance_is_within_it() -> None:
    r = report_for(
        revised_series(N, [10.0] * N, factors=(0.8, 1.0)), config=TrustConfig(tolerance=0.2)
    )
    assert r.lags[0].share_material == 0.0 and r.lags[0].share_changed == 1.0
    assert r.settled_lag_median == 0


@pytest.mark.parametrize(("factor", "flagged"), [(0.95, True), (0.985, False), (1.03, True)])
def test_the_first_release_bias_is_flagged_beyond_two_percent(factor: float, flagged: bool) -> None:
    r = report_for(revised_series(N, [10.0] * N, factors=(factor, 1.0)))
    assert any("biased" in f.lower() for f in r.findings) is flagged


def test_a_first_release_of_zero_has_no_ratio_and_does_not_break_the_report() -> None:
    r = report_for(revised_series(N, [10.0] * N, factors=(0.0, 1.0)))
    assert r.lags[0].median_ratio is None and r.lags[0].share_changed == 1.0
    assert r.years == []


def test_a_series_that_is_never_revised_has_nothing_to_flag() -> None:
    r = report_for(revised_series(N, varying_truth, factors=(1.0,)))
    assert r.lags[0].share_changed == 0.0
    assert r.settled_lag_median == 0
    assert not any("biased" in f.lower() or "drift" in f.lower() for f in r.findings)
    assert r.n_versions == N


def test_slow_settling_is_flagged() -> None:
    factors = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0)  # final only at the seventh report
    r = report_for(revised_series(N, [10.0] * N, factors=factors))
    assert r.settled_lag_median == 6 and r.settled_lag_p90 == 6
    assert any("slow" in f.lower() for f in r.findings)


def test_a_period_that_has_not_settled_within_the_window_is_reported_as_such() -> None:
    factors = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0)
    r = report_for(revised_series(N, [10.0] * N, factors=factors), config=TrustConfig(settle=5))
    assert r.settled_lag_p90 == 6  # settle + 1: not within the window of 5
    text = " ".join(r.findings) + render_trust_markdown(r)
    assert "more than 5 periods" in text


def test_missing_periods_are_counted_and_flagged() -> None:
    observations = [o for o in revised_series(N, varying_truth) if o.event_time != week(10)]
    r = report_for(observations)
    assert r.missing_periods == 1 and r.n_periods == N - 1
    assert any("missing" in f.lower() for f in r.findings)


def test_the_report_only_uses_what_was_known_at_the_chosen_time() -> None:
    moment = report_time(80)
    clean = revised_series(N, varying_truth)
    poisoned = [
        *clean,
        *[Observation("rev", week(w), report_time(90), 1e6, "t") for w in range(60)],
    ]
    a = report_for(clean, as_of=moment)
    b = report_for(poisoned, as_of=moment)
    assert a == b
    assert a.as_of == moment and a.last_period == week(80)


def test_an_empty_series_gives_an_empty_report() -> None:
    r = build_trust_report(VintageTable.from_observations([]), "nothing")
    assert (r.n_periods, r.n_versions, r.lags, r.years, r.as_of) == (0, 0, [], [], None)
    assert r.findings == ["No data: the series has no values."]


def test_a_short_series_reports_that_nothing_is_settled() -> None:
    r = report_for(revised_series(10, varying_truth))
    assert r.n_settled == 0 and r.lags[0].n == 0 and r.lags[0].median_ratio is None
    assert any("settled" in f.lower() for f in r.findings)


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_lag": -1},
        {"settle": 0},
        {"tolerance": -0.1},
        {"step_days": 0},
        {"min_year_samples": 0},
    ],
)
def test_invalid_configuration_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TrustConfig(**overrides)  # type: ignore[arg-type]


def test_markdown_and_json_carry_the_same_numbers() -> None:
    r = report_for(revised_series(N, varying_truth))
    markdown = render_trust_markdown(r)
    assert "# Trust report: rev" in markdown
    assert "1.250" in markdown and "104" in markdown
    assert "## Findings" in markdown and "## Bias by year" in markdown
    data = json.loads(json.dumps(trust_report_to_dict(r)))
    assert data["series_id"] == "rev" and data["n_settled"] == 104
    assert data["lags"][0]["median_ratio"] == pytest.approx(1.25)
    assert data["as_of"] == report_time(N - 1).isoformat()
    assert data["days_to_final_median"] == pytest.approx(14.0)
