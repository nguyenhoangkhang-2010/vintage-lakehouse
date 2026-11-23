"""Tests for estimating the revision correction.

Hand-computed on the series of ``backfill_fixture``: with factors (0.8, 0.9, 1.0) the settled
value divided by a first release is 1.25 and by a second release 1 / 0.9.
"""

from __future__ import annotations

import pytest

from backfill_fixture import (
    FACTORS,
    make_store,
    report_time,
    revised_series,
    varying_truth,
    week,
)
from vintage_lakehouse.backfill import CorrectionConfig, VintageTable, estimate_correction
from vintage_lakehouse.storage import Observation

N = 30
LAST = 20  # the latest reported week at the cutoff used in most tests


def cfg(**overrides: object) -> CorrectionConfig:
    fields: dict[str, object] = {"settle": 3, "max_lag": 3, "min_samples": 5}
    fields.update(overrides)
    return CorrectionConfig(**fields)  # type: ignore[arg-type]


def vintages(observations: list[Observation]) -> VintageTable:
    return VintageTable.from_observations(observations)


def test_ratio_factors_are_hand_computed() -> None:
    table = vintages(revised_series(N, varying_truth))
    factors = estimate_correction(table, report_time(LAST), cfg())
    assert factors.adjustment == {
        0: pytest.approx(1.25),
        1: pytest.approx(1 / 0.9),
        2: pytest.approx(1.0),
    }
    # weeks 0..LAST-3 are settled, and each contributes one pair per lag
    assert factors.samples == {0: LAST - 2, 1: LAST - 2, 2: LAST - 2}


def test_applying_the_ratio_recovers_the_settled_value() -> None:
    factors = estimate_correction(
        vintages(revised_series(N, varying_truth)), report_time(LAST), cfg()
    )
    assert factors.apply(8.0, 0) == pytest.approx(10.0)
    assert factors.apply(9.0, 1) == pytest.approx(10.0)
    assert factors.apply(10.0, 2) == pytest.approx(10.0)
    assert factors.apply(10.0, 3) == 10.0  # beyond max_lag: untouched


def test_additive_offsets_on_a_flat_series() -> None:
    table = vintages(revised_series(N, [10.0] * N))
    factors = estimate_correction(table, report_time(LAST), cfg(kind="additive"))
    assert factors.adjustment == {0: pytest.approx(2.0), 1: pytest.approx(1.0), 2: 0.0}
    assert factors.apply(8.0, 0) == pytest.approx(10.0)


def test_additive_correction_is_floored() -> None:
    table = vintages(revised_series(N, [10.0] * N, factors=(2.0, 1.0)))  # first release too high
    factors = estimate_correction(table, report_time(LAST), cfg(kind="additive", max_lag=2))
    assert factors.adjustment[0] == pytest.approx(-10.0)
    assert factors.apply(20.0, 0) == pytest.approx(10.0)
    assert factors.apply(4.0, 0) == 0.0  # would be -6, floored at 0


def test_the_median_is_used_not_the_mean() -> None:
    ratios = [1.0, 1.0, 1.1, 1.2, 1.3, 5.0]  # settled / first release for weeks 0..5
    table = vintages(
        revised_series(8, [10.0] * 8, factors=lambda w: (1 / ratios[w] if w < 6 else 1.0, 1.0))
    )
    factors = estimate_correction(
        table, report_time(7), CorrectionConfig(settle=2, max_lag=1, min_samples=1)
    )
    assert factors.samples[0] == 6
    assert factors.adjustment[0] == pytest.approx(1.15)  # median; the mean would be 1.767


def test_lags_with_too_few_pairs_are_not_corrected() -> None:
    table = vintages(revised_series(N, varying_truth))
    exact = LAST - 2
    enough = estimate_correction(table, report_time(LAST), cfg(min_samples=exact))
    short = estimate_correction(table, report_time(LAST), cfg(min_samples=exact + 1))
    assert enough.adjustment[0] == pytest.approx(1.25)
    assert short.adjustment == {0: 1.0, 1: 1.0, 2: 1.0}
    assert short.samples[0] == exact  # reported even when not used


def test_a_series_that_is_never_revised_gets_no_correction() -> None:
    table = vintages(revised_series(N, varying_truth, factors=(1.0,)))
    factors = estimate_correction(table, report_time(LAST), cfg())
    assert factors.adjustment == {0: 1.0, 1: 1.0, 2: 1.0}
    kind_additive = estimate_correction(table, report_time(LAST), cfg(kind="additive"))
    assert kind_additive.adjustment == {0: 0.0, 1: 0.0, 2: 0.0}


def test_ratio_skips_pairs_whose_provisional_value_is_not_positive() -> None:
    truth = [0.0 if w in (2, 3) else 10.0 for w in range(N)]
    table = vintages(revised_series(N, truth))
    factors = estimate_correction(table, report_time(LAST), cfg())
    assert factors.samples == {0: LAST - 4, 1: LAST - 4, 2: LAST - 4}
    assert factors.adjustment[0] == pytest.approx(1.25)


def test_a_later_period_released_before_an_earlier_one_forms_no_pair() -> None:
    # week 0 is first reported at report 5, but week 1 already at report 1 (an odd source), so the
    # clock for week 0 at lag 1 is before week 0 existed
    observations = [
        *revised_series(N, [10.0] * N, factors=(0.8, 1.0))[2:],  # drops week 0's versions
        Observation("rev", week(0), report_time(5), 7.0, "test"),
        Observation("rev", week(0), report_time(6), 10.0, "test"),
    ]
    factors = estimate_correction(
        vintages(observations),
        report_time(LAST),
        CorrectionConfig(settle=3, max_lag=2, min_samples=1),
    )
    assert factors.samples[0] == LAST - 2  # weeks 0..17 all form a lag-0 pair
    assert factors.samples[1] == LAST - 3  # week 0 forms no lag-1 pair
    assert factors.adjustment[0] == pytest.approx(1.25)


def test_reversed_lags_apply_the_factors_to_the_wrong_values() -> None:
    table = vintages(revised_series(N, varying_truth))
    placebo = estimate_correction(table, report_time(LAST), cfg(reverse_lags=True))
    assert placebo.apply(8.0, 0) == pytest.approx(8.0 * 1.0)  # lag 0 gets lag 2's factor
    assert placebo.apply(9.0, 1) == pytest.approx(9.0 / 0.9)  # the middle lag is unchanged
    assert placebo.apply(10.0, 2) == pytest.approx(10.0 * 1.25)  # lag 2 gets lag 0's factor
    assert placebo.apply(10.0, 3) == 10.0


def test_nothing_is_known_before_the_first_release() -> None:
    table = vintages(revised_series(N, varying_truth))
    factors = estimate_correction(table, report_time(0).replace(year=2000), cfg())
    assert factors.adjustment == {0: 1.0, 1: 1.0, 2: 1.0}
    assert factors.samples == {0: 0, 1: 0, 2: 0}


# --- no look-ahead -----------------------------------------------------------------------


def test_versions_published_after_the_cutoff_change_nothing() -> None:
    clean = revised_series(N, varying_truth)
    cutoff = report_time(LAST)
    poisoned = [
        *clean,
        # wild revisions of every settled week (enough to move a median), and of the latest week,
        # all after the cutoff
        *[Observation("rev", week(w), report_time(LAST + 5), 1e6, "test") for w in range(LAST - 2)],
        Observation("rev", week(LAST), report_time(LAST + 6), -1e6, "test"),
        # and a whole new period that is only released after the cutoff
        Observation("rev", week(LAST + 4), report_time(LAST + 4), 1e9, "test"),
    ]
    a = estimate_correction(vintages(clean), cutoff, cfg())
    b = estimate_correction(vintages(poisoned), cutoff, cfg())
    assert a == b


def test_a_pair_is_not_formed_from_a_report_that_does_not_exist_yet() -> None:
    # With settle=1 and max_lag=3, week LAST-1 at lag 2 would need the report of week LAST + 1,
    # which is published after the cutoff and is in the table only because it was loaded in full.
    table = vintages(revised_series(N, varying_truth))
    factors = estimate_correction(
        table, report_time(LAST), CorrectionConfig(settle=1, max_lag=3, min_samples=1)
    )
    assert factors.samples == {0: LAST, 1: LAST, 2: LAST - 1}


def test_a_store_cut_at_the_cutoff_gives_the_same_factors_as_the_full_store() -> None:
    observations = revised_series(N, varying_truth)
    cutoff = report_time(LAST)
    full = VintageTable.from_store(make_store(observations), "rev", report_time(N + 5))
    cut = VintageTable.from_store(make_store(observations), "rev", cutoff)
    assert estimate_correction(full, cutoff, cfg()) == estimate_correction(cut, cutoff, cfg())


def test_more_history_changes_the_estimate_only_through_data_up_to_the_cutoff() -> None:
    # A bias that appears from week 12 on must not be visible at a cutoff before week 12 reports.
    def factors_for(w: int) -> tuple[float, ...]:
        return (0.8, 1.0) if w < 12 else (0.5, 1.0)

    table = vintages(revised_series(N, [10.0] * N, factors=factors_for))
    early = estimate_correction(table, report_time(11), cfg(max_lag=2, settle=2))
    late = estimate_correction(table, report_time(29), cfg(max_lag=2, settle=2))
    assert early.adjustment[0] == pytest.approx(1.25)
    assert late.adjustment[0] == pytest.approx(2.0)  # the median of 12 x 1.25 and 16 x 2.0


# --- configuration ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"kind": "multiplicative"}, "kind"),
        ({"max_lag": 0}, "max_lag"),
        ({"settle": 0}, "settle"),
        ({"min_samples": 0}, "min_samples"),
        ({"step_days": 0}, "step_days"),
    ],
)
def test_invalid_configuration_is_rejected(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        cfg(**overrides)


def test_defaults_are_the_ones_fixed_in_adr_0010() -> None:
    config = CorrectionConfig()
    assert (config.kind, config.max_lag, config.settle, config.min_samples) == (
        "ratio",
        4,
        26,
        30,
    )
    assert config.reverse_lags is False
    assert FACTORS == (0.8, 0.9, 1.0)  # the fixture the hand calculations above rely on
