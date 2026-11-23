"""Tests for the backfill-recovery statistics and runner (hand-computed where possible)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from experiment_fixture import SERIES, at, revised_store, small_config
from vintage_lakehouse.backfill import CorrectionConfig
from vintage_lakehouse.experiment.recovery import (
    VARIANTS,
    RecoveryConfig,
    RecoveryEstimate,
    analyze_recovery,
    corrected_name,
    run_recovery,
    run_recovery_analyses,
    variant_config,
)
from vintage_lakehouse.forecast import SeasonalNaive
from vintage_lakehouse.replay import Mode, ReplayConfig, ScoredForecast, run_replay

UTC = at(0).tzinfo
O1, O2 = datetime(2024, 1, 6, tzinfo=UTC), datetime(2024, 2, 3, tzinfo=UTC)


def row(model: str, origin: datetime, mode: Mode, wis: float, horizon: int = 1) -> ScoredForecast:
    return ScoredForecast(
        model=model,
        origin=origin,
        horizon=horizon,
        target=date(2024, 3, 1),
        mode=mode,
        truth=1.0,
        median=1.0,
        wis=wis,
        abs_error=0.0,
        coverage=(),
    )


def base_rows(
    a: dict[datetime, float], b: dict[datetime, float], model: str = "m"
) -> list[ScoredForecast]:
    return [row(model, o, Mode.REALTIME, a[o]) for o in a] + [
        row(model, o, Mode.HINDSIGHT, b[o]) for o in b
    ]


def corrected_rows(
    c: dict[datetime, float], variant: str = "backfill", model: str = "m"
) -> list[ScoredForecast]:
    return [row(corrected_name(model, variant), o, Mode.REALTIME, c[o]) for o in c]


IDENTITY = [[0, 1]]  # one "resample" that is the sample itself


def pooled(
    analysis_estimates: list[RecoveryEstimate], variant: str = "backfill"
) -> RecoveryEstimate:
    return next(e for e in analysis_estimates if e.variant == variant and e.horizon is None)


def test_improvement_and_recovery_are_hand_computed() -> None:
    # a = (4, 6), b = (1, 3), c = (2, 4): gap 6, improvement 4, recovery 2/3
    result = analyze_recovery(
        base_rows({O1: 4.0, O2: 6.0}, {O1: 1.0, O2: 3.0}),
        corrected_rows({O1: 2.0, O2: 4.0}),
        replicates=IDENTITY,
    )
    e = pooled(result.estimates)
    assert (e.model, e.variant, e.n_pairs) == ("m", "backfill", 2)
    assert (e.wis_realtime, e.wis_hindsight, e.wis_corrected) == (5.0, 2.0, 3.0)
    assert e.improvement == pytest.approx(2.0)
    assert e.relative_improvement == pytest.approx(4 / 10)
    assert e.recovery == pytest.approx(4 / 6)
    assert e.recovery_low == pytest.approx(4 / 6)
    assert e.recovery_high == pytest.approx(4 / 6)
    assert result.gaps["m"].gap == pytest.approx(6 / 10)


def test_recovery_can_exceed_one_and_can_be_negative() -> None:
    better = analyze_recovery(
        base_rows({O1: 4.0, O2: 6.0}, {O1: 1.0, O2: 3.0}),
        corrected_rows({O1: 0.0, O2: 0.0}),
        replicates=IDENTITY,
    )
    assert pooled(better.estimates).recovery == pytest.approx(10 / 6)
    worse = analyze_recovery(
        base_rows({O1: 4.0, O2: 6.0}, {O1: 1.0, O2: 3.0}),
        corrected_rows({O1: 7.0, O2: 9.0}),
        replicates=IDENTITY,
    )
    assert pooled(worse.estimates).recovery == pytest.approx(-6 / 6)
    assert pooled(worse.estimates).improvement == pytest.approx(-3.0)


def test_recovery_is_withheld_when_the_gap_interval_includes_zero() -> None:
    # origin 1 has a negative gap, origin 2 a positive one: resampling origin 1 twice gives a
    # negative pooled gap, so the interval of the gap includes zero
    result = analyze_recovery(
        base_rows({O1: 1.0, O2: 4.0}, {O1: 3.0, O2: 1.0}),
        corrected_rows({O1: 1.0, O2: 2.0}),
        replicates=[[0, 0], [1, 1], [0, 1]],
    )
    e = pooled(result.estimates)
    assert result.gaps["m"].gap_low is not None and result.gaps["m"].gap_low < 0.0
    assert (e.recovery, e.recovery_low, e.recovery_high, e.recovery_valid) == (None, None, None, 0)
    assert e.improvement == pytest.approx(1.0)  # still reported


def test_recovery_is_withheld_when_the_point_gap_is_not_positive() -> None:
    result = analyze_recovery(
        base_rows({O1: 1.0, O2: 1.0}, {O1: 3.0, O2: 3.0}),
        corrected_rows({O1: 1.0, O2: 1.0}),
        replicates=IDENTITY,
    )
    assert pooled(result.estimates).recovery is None


def test_an_origin_drawn_twice_counts_twice() -> None:
    # improvements 1 (origin 1) and 4 (origin 2) -> resamples give 1, 4 and 2.5
    result = analyze_recovery(
        base_rows({O1: 4.0, O2: 6.0}, {O1: 1.0, O2: 3.0}),
        corrected_rows({O1: 3.0, O2: 2.0}),
        replicates=[[0, 0], [1, 1], [0, 1]],
        level=0.5,
    )
    e = pooled(result.estimates)
    assert e.improvement == pytest.approx(2.5)
    assert e.improvement_low == pytest.approx(1.75)
    assert e.improvement_high == pytest.approx(3.25)


def test_horizons_and_pooled_estimates_are_both_reported() -> None:
    a = {O1: 4.0, O2: 6.0}
    rows_base = base_rows(a, {O1: 1.0, O2: 3.0})
    rows_base += [row("m", O1, Mode.REALTIME, 10.0, 2), row("m", O1, Mode.HINDSIGHT, 4.0, 2)]
    rows_corrected = [
        *corrected_rows({O1: 2.0, O2: 4.0}),
        row(corrected_name("m", "backfill"), O1, Mode.REALTIME, 8.0, 2),
    ]
    result = analyze_recovery(rows_base, rows_corrected, replicates=IDENTITY)
    by_horizon = {e.horizon: e for e in result.estimates}
    assert set(by_horizon) == {1, 2, None}
    assert (
        by_horizon[1].n_pairs == 2 and by_horizon[2].n_pairs == 1 and by_horizon[None].n_pairs == 3
    )
    assert by_horizon[2].improvement == pytest.approx(2.0)
    assert by_horizon[None].improvement == pytest.approx(6.0 / 3)
    # one resample (the sample itself): the interval collapses onto the estimate, and the
    # improvement is a mean over pairs, not over origins
    assert by_horizon[None].improvement_low == pytest.approx(2.0)
    assert by_horizon[None].improvement_high == pytest.approx(2.0)


def test_only_matched_triples_are_used() -> None:
    # no hindsight score for origin 2, and no corrected score for origin 3
    o3 = datetime(2024, 3, 2, tzinfo=UTC)
    rows_base = [
        row("m", O1, Mode.REALTIME, 4.0),
        row("m", O1, Mode.HINDSIGHT, 1.0),
        row("m", O2, Mode.REALTIME, 100.0),
        row("m", o3, Mode.REALTIME, 100.0),
        row("m", o3, Mode.HINDSIGHT, 100.0),
    ]
    result = analyze_recovery(
        rows_base, corrected_rows({O1: 2.0, O2: 0.0}), replicates=[[0, 1, 2]], level=0.9
    )
    e = pooled(result.estimates)
    assert e.n_pairs == 1
    assert e.improvement == pytest.approx(2.0)


def test_variants_are_compared_with_the_primary_variant() -> None:
    rows_base = base_rows({O1: 4.0, O2: 6.0}, {O1: 1.0, O2: 3.0})
    rows_corrected = (
        corrected_rows({O1: 2.0, O2: 4.0}, "backfill")  # improvement 2.0 per pair
        + corrected_rows({O1: 4.0, O2: 6.0}, "backfill-placebo")  # improvement 0
    )
    result = analyze_recovery(rows_base, rows_corrected, replicates=IDENTITY)
    [c] = result.comparisons
    assert (c.model, c.variant, c.versus) == ("m", "backfill-placebo", "backfill")
    assert c.difference == pytest.approx(-2.0)
    assert c.low == pytest.approx(-2.0)
    assert c.high == pytest.approx(-2.0)


def test_models_are_analysed_separately() -> None:
    rows_base = base_rows({O1: 4.0, O2: 6.0}, {O1: 1.0, O2: 3.0}, "x") + base_rows(
        {O1: 2.0, O2: 2.0}, {O1: 2.0, O2: 2.0}, "y"
    )
    rows_corrected = corrected_rows({O1: 2.0, O2: 4.0}, model="x") + corrected_rows(
        {O1: 1.0, O2: 1.0}, model="y"
    )
    result = analyze_recovery(rows_base, rows_corrected, replicates=IDENTITY)
    x = next(e for e in result.estimates if e.model == "x" and e.horizon is None)
    y = next(e for e in result.estimates if e.model == "y" and e.horizon is None)
    assert x.recovery == pytest.approx(4 / 6)
    assert y.recovery is None  # no gap to recover
    assert y.improvement == pytest.approx(1.0)


def test_recovery_is_withheld_when_the_lower_limit_of_the_gap_is_exactly_zero() -> None:
    # origin 1 has no gap; two of the four resamples contain only origin 1, so the lower limit of
    # the gap interval is exactly 0, which does not exclude zero
    result = analyze_recovery(
        base_rows({O1: 2.0, O2: 4.0}, {O1: 2.0, O2: 1.0}),
        corrected_rows({O1: 2.0, O2: 2.0}),
        replicates=[[0, 0], [0, 0], [0, 1], [1, 1]],
        level=0.99,
    )
    assert result.gaps["m"].gap_low == 0.0
    assert pooled(result.estimates).recovery is None


def test_recovery_is_withheld_when_the_point_gap_is_negative_even_if_the_interval_is_not() -> None:
    # the resamples only ever draw origin 1 (positive gap) but the sample as a whole has none
    result = analyze_recovery(
        base_rows({O1: 4.0, O2: 1.0}, {O1: 1.0, O2: 5.0}),
        corrected_rows({O1: 2.0, O2: 1.0}),
        replicates=[[0, 0], [0, 0]],
    )
    assert result.gaps["m"].gap_low is not None and result.gaps["m"].gap_low > 0.0
    assert result.gaps["m"].gap is not None and result.gaps["m"].gap < 0.0
    assert pooled(result.estimates).recovery is None


def test_a_perfect_base_model_has_no_relative_improvement_to_report() -> None:
    result = analyze_recovery(
        base_rows({O1: 0.0, O2: 0.0}, {O1: 0.0, O2: 0.0}),
        corrected_rows({O1: 0.0, O2: 0.0}),
        replicates=IDENTITY,
    )
    e = pooled(result.estimates)
    assert (e.relative_improvement, e.relative_low, e.recovery) == (None, None, None)
    assert e.improvement == 0.0


def test_corrected_scores_in_hindsight_mode_are_ignored() -> None:
    result = analyze_recovery(
        base_rows({O1: 4.0, O2: 6.0}, {O1: 1.0, O2: 3.0}),
        [
            *corrected_rows({O1: 2.0, O2: 4.0}),
            row(corrected_name("m", "backfill"), O1, Mode.HINDSIGHT, 0.0),
            row(corrected_name("m", "backfill"), O2, Mode.HINDSIGHT, 0.0),
        ],
        replicates=IDENTITY,
    )
    e = pooled(result.estimates)
    assert e.n_pairs == 2 and e.improvement == pytest.approx(2.0)


def test_model_names_that_contain_a_plus_sign_are_split_at_the_last_one() -> None:
    result = analyze_recovery(
        base_rows({O1: 4.0}, {O1: 1.0}, "x+y"),
        corrected_rows({O1: 2.0}, model="x+y"),
        replicates=[[0]],
    )
    assert {(e.model, e.variant) for e in result.estimates} == {("x+y", "backfill")}


def test_variants_are_compared_resample_by_resample() -> None:
    # improvements per resample: backfill 1, 4, 2.5 and placebo 4, 1, 2.5, so the paired
    # differences are 3, -3, 0 (sorting each side first would wrongly give 0, 0, 0)
    rows_base = base_rows({O1: 4.0, O2: 6.0}, {O1: 1.0, O2: 3.0})
    rows_corrected = corrected_rows({O1: 3.0, O2: 2.0}, "backfill") + corrected_rows(
        {O1: 0.0, O2: 5.0}, "backfill-placebo"
    )
    result = analyze_recovery(
        rows_base, rows_corrected, replicates=[[0, 0], [1, 1], [0, 1]], level=0.5
    )
    [c] = result.comparisons
    assert c.difference == pytest.approx(0.0)
    assert c.low == pytest.approx(-1.5)
    assert c.high == pytest.approx(1.5)


def test_no_rows_give_an_empty_analysis() -> None:
    result = analyze_recovery([], [], replicates=[])
    assert (result.n_origins, result.estimates, result.comparisons) == (0, [], [])


def test_replicates_must_refer_to_existing_origins() -> None:
    with pytest.raises(ValueError, match="do not exist"):
        analyze_recovery(
            base_rows({O1: 4.0}, {O1: 1.0}), corrected_rows({O1: 2.0}), replicates=[[0, 5]]
        )


# --- configuration ---------------------------------------------------------------------


def test_variants_are_distinct_settings_of_the_same_correction() -> None:
    base = CorrectionConfig()
    assert variant_config("backfill", base) == base
    additive = variant_config("backfill-additive", base)
    assert additive.kind == "additive" and not additive.reverse_lags
    placebo = variant_config("backfill-placebo", base)
    assert placebo.reverse_lags and placebo.kind == "ratio"
    with pytest.raises(ValueError, match="unknown variant"):
        variant_config("magic", base)


@pytest.mark.parametrize(
    "overrides",
    [{"variants": ()}, {"variants": ("backfill", "backfill")}, {"variants": ("magic",)}],
)
def test_invalid_recovery_configuration_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RecoveryConfig(experiment=small_config(), **overrides)  # type: ignore[arg-type]


def test_the_correction_and_the_experiment_must_agree_on_the_step() -> None:
    with pytest.raises(ValueError, match="step_days"):
        RecoveryConfig(experiment=small_config(), correction=CorrectionConfig(step_days=1))


# --- end to end on the small revised series ----------------------------------------------


def make_config(**overrides: object) -> RecoveryConfig:
    return RecoveryConfig(experiment=small_config(**overrides))


def test_the_run_scores_base_models_in_both_modes_and_corrected_models_in_real_time() -> None:
    config = make_config()
    result = run_recovery(revised_store(), config)
    assert {r.model for r in result.base_rows} == {"seasonal_naive"}
    assert {r.mode for r in result.base_rows} == {Mode.REALTIME, Mode.HINDSIGHT}
    assert {r.model for r in result.corrected_rows} == {
        corrected_name("seasonal_naive", v) for v in VARIANTS
    }
    assert {r.mode for r in result.corrected_rows} == {Mode.REALTIME}
    assert len(result.corrected_rows) == 3 * len(
        [r for r in result.base_rows if r.mode is Mode.REALTIME]
    )


def test_the_base_scores_are_those_of_a_plain_replay() -> None:
    config = make_config()
    result = run_recovery(revised_store(), config)
    experiment = config.experiment
    plain = run_replay(
        revised_store(),
        [SeasonalNaive(lower_bound=experiment.lower_bound)],
        ReplayConfig(
            series_id=SERIES,
            origins=experiment.origins(),
            horizons=experiment.horizons,
            evaluation_time=experiment.evaluation_time,
            step=timedelta(days=7),
            min_history=1,
        ),
    )
    assert result.base_rows == plain.rows


def test_the_estimated_first_release_factor_is_the_known_one() -> None:
    result = run_recovery(revised_store(), make_config())
    factors = next(iter(result.factors_by_origin.values()))
    assert factors.adjustment[0] == pytest.approx(1 / 0.9)  # first release is 10% too low
    assert factors.adjustment[1] == pytest.approx(1.0)
    assert set(result.factors_by_origin) == set(make_config().experiment.origins())


def test_correcting_a_constant_bias_does_not_hurt_and_the_placebo_does_not_help_more() -> None:
    config = make_config(n_boot=50)
    result = run_recovery(revised_store(), config)
    analysis = next(iter(run_recovery_analyses(result).values()))
    pooled_by_variant = {
        e.variant: e
        for e in analysis.estimates
        if e.horizon is None and e.model == "seasonal_naive"
    }
    assert pooled_by_variant["backfill"].improvement >= -1e-12
    assert (
        pooled_by_variant["backfill"].improvement
        >= pooled_by_variant["backfill-placebo"].improvement
    )


def test_scenarios_are_all_origins_and_without_the_exclusion() -> None:
    result = run_recovery(revised_store(), make_config(n_boot=20))
    analyses = run_recovery_analyses(result)
    labels = [s.label for s in analyses]
    assert len(labels) == 2 and "excluding" in labels[1]
    all_origins, without = analyses.values()
    assert without.n_origins < all_origins.n_origins


def test_running_twice_gives_identical_results() -> None:
    first = run_recovery(revised_store(), make_config(n_boot=20))
    second = run_recovery(revised_store(), make_config(n_boot=20))
    assert first.base_rows == second.base_rows
    assert first.corrected_rows == second.corrected_rows
    assert run_recovery_analyses(first) == run_recovery_analyses(second)


def test_a_series_without_data_gives_no_rows() -> None:
    result = run_recovery(revised_store(), make_config(series_id="nothing/here"))
    assert result.base_rows == [] and result.corrected_rows == [] and result.factors_by_origin == {}
