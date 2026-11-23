"""Tests for the leakage-gap statistics, on pairs whose answers can be worked out by hand."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from vintage_lakehouse.experiment.analysis import (
    GapEstimate,
    analyze,
    analyze_rows,
    revision_analysis,
    spearman,
)
from vintage_lakehouse.experiment.bootstrap import make_replicates
from vintage_lakehouse.replay import Mode, ScoredForecast

UTC = timezone.utc
START = datetime(2023, 1, 1, tzinfo=UTC)


def origin(i: int) -> datetime:
    return START + timedelta(weeks=i)


def row(model: str, i: int, horizon: int, mode: Mode, wis: float) -> ScoredForecast:
    return ScoredForecast(
        model=model,
        origin=origin(i),
        horizon=horizon,
        target=date(2023, 6, 1) + timedelta(weeks=i + horizon),
        mode=mode,
        truth=1.0,
        median=1.0,
        wis=wis,
        abs_error=0.0,
        coverage=(),
    )


def pair(
    model: str, i: int, horizon: int, realtime: float, hindsight: float
) -> list[ScoredForecast]:
    return [
        row(model, i, horizon, Mode.REALTIME, realtime),
        row(model, i, horizon, Mode.HINDSIGHT, hindsight),
    ]


def constant_rows(model: str = "m", n: int = 10) -> list[ScoredForecast]:
    """Horizon 1: WIS 2 -> 1 (gap 0.5). Horizon 2: WIS 4 -> 3 (gap 0.25). Pooled gap 1/3."""
    rows: list[ScoredForecast] = []
    for i in range(n):
        rows += pair(model, i, 1, 2.0, 1.0)
        rows += pair(model, i, 2, 4.0, 3.0)
    return rows


def by_horizon(estimates: list[GapEstimate], horizon: int | None) -> GapEstimate:
    return next(e for e in estimates if e.horizon == horizon)


def test_point_estimates_match_the_hand_calculation() -> None:
    analysis = analyze_rows(constant_rows(), n_boot=50, block_length=3, seed=0)
    one, two, pooled = (by_horizon(analysis.estimates, h) for h in (1, 2, None))

    assert (one.n_pairs, two.n_pairs, pooled.n_pairs) == (10, 10, 20)
    assert one.gap == pytest.approx(0.5)
    assert two.gap == pytest.approx(0.25)
    assert pooled.gap == pytest.approx(1 / 3)
    assert (one.wis_realtime, one.wis_hindsight) == (2.0, 1.0)
    assert pooled.wis_realtime == pytest.approx(3.0)
    assert pooled.wis_hindsight == pytest.approx(2.0)
    assert one.degradation == pytest.approx(1.0)
    assert pooled.degradation == pytest.approx(1.0)


def test_estimates_are_listed_by_model_then_horizon_then_pooled() -> None:
    rows = constant_rows("first") + constant_rows("second")
    analysis = analyze_rows(rows, n_boot=10, block_length=2, seed=0)
    assert [(e.model, e.horizon) for e in analysis.estimates] == [
        ("first", 1),
        ("first", 2),
        ("first", None),
        ("second", 1),
        ("second", 2),
        ("second", None),
    ]
    assert analysis.n_origins == 10


def test_constant_pairs_give_a_degenerate_interval() -> None:
    analysis = analyze_rows(constant_rows(), n_boot=50, block_length=3, seed=0)
    pooled = by_horizon(analysis.estimates, None)
    assert pooled.gap_low == pytest.approx(pooled.gap)
    assert pooled.gap_high == pytest.approx(pooled.gap)
    assert pooled.degradation_low == pytest.approx(1.0)


def varied_rows(n: int = 40) -> list[ScoredForecast]:
    rows: list[ScoredForecast] = []
    for i in range(n):
        realtime = 1.0 + (i % 7) * 0.3
        rows += pair("m", i, 1, realtime, realtime * (0.9 - 0.02 * (i % 5)))
    return rows


def test_the_interval_contains_the_estimate_and_has_width() -> None:
    estimate = by_horizon(
        analyze_rows(varied_rows(), n_boot=300, block_length=4, seed=1).estimates, 1
    )
    assert estimate.gap is not None
    assert estimate.gap_low is not None
    assert estimate.gap_high is not None
    assert estimate.gap_low < estimate.gap < estimate.gap_high
    assert estimate.degradation_low < estimate.degradation < estimate.degradation_high


def test_a_wider_confidence_level_gives_a_wider_interval() -> None:
    rows = varied_rows()
    narrow = by_horizon(
        analyze_rows(rows, n_boot=300, block_length=4, level=0.5, seed=1).estimates, 1
    )
    wide = by_horizon(
        analyze_rows(rows, n_boot=300, block_length=4, level=0.95, seed=1).estimates, 1
    )
    assert narrow.gap_low is not None and narrow.gap_high is not None
    assert wide.gap_low is not None and wide.gap_high is not None
    assert wide.gap_high - wide.gap_low > narrow.gap_high - narrow.gap_low


def test_the_same_seed_gives_the_same_result_and_another_seed_a_different_one() -> None:
    rows = varied_rows()
    first = analyze_rows(rows, n_boot=200, block_length=4, seed=5)
    again = analyze_rows(rows, n_boot=200, block_length=4, seed=5)
    other = analyze_rows(rows, n_boot=200, block_length=4, seed=6)
    assert first == again
    assert first.estimates[0].gap_low != other.estimates[0].gap_low
    assert (
        first.estimates[0].gap == other.estimates[0].gap
    )  # the point estimate never depends on it


def test_only_complete_pairs_are_counted() -> None:
    rows = [*constant_rows(), row("m", 20, 1, Mode.REALTIME, 99.0)]  # real-time only
    rows = [
        r
        for r in rows
        if not (r.origin == origin(9) and r.horizon == 2 and r.mode is Mode.HINDSIGHT)
    ]
    analysis = analyze_rows(rows, n_boot=20, block_length=2, seed=0)
    assert by_horizon(analysis.estimates, 2).n_pairs == 9
    assert by_horizon(analysis.estimates, 1).n_pairs == 10


def test_a_model_that_is_perfect_in_real_time_has_no_defined_gap() -> None:
    rows = [r for i in range(8) for r in pair("perfect", i, 1, 0.0, 0.0)]
    estimate = by_horizon(analyze_rows(rows, n_boot=20, block_length=2, seed=0).estimates, 1)
    assert estimate.gap is None
    assert estimate.gap_low is None and estimate.gap_high is None
    assert estimate.degradation == 0.0


def test_models_are_compared_on_the_same_resamples() -> None:
    rows = constant_rows("big") + [r for i in range(10) for r in pair("small", i, 1, 4.0, 3.0)]
    # big pooled gap 1/3, small gap 0.25.
    [comparison] = analyze_rows(rows, n_boot=50, block_length=3, seed=0).comparisons
    assert (comparison.model_a, comparison.model_b) == ("big", "small")
    assert comparison.difference == pytest.approx(1 / 3 - 0.25)
    assert comparison.low == pytest.approx(comparison.difference)
    assert comparison.high == pytest.approx(comparison.difference)
    assert comparison.n_valid == 50


def test_a_single_model_has_no_comparisons() -> None:
    assert analyze_rows(constant_rows(), n_boot=10, block_length=2, seed=0).comparisons == []


def test_no_rows_means_an_empty_analysis() -> None:
    analysis = analyze_rows([], n_boot=10, block_length=2, seed=0)
    assert (analysis.n_origins, analysis.estimates, analysis.comparisons) == (0, [], [])


def test_replicates_must_refer_to_existing_origins() -> None:
    with pytest.raises(ValueError, match="do not exist"):
        analyze(constant_rows(n=5), replicates=[[0, 1, 9]])


# --- Spearman correlation and revision size ----------------------------------------------


def test_spearman_of_monotone_data_is_plus_or_minus_one() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 2, 3, 4], [1, 8, 27, 64]) == pytest.approx(1.0)  # any increasing shape


def test_spearman_uses_average_ranks_for_ties() -> None:
    # x ranks are 1, 2.5, 2.5, 4 and y ranks are 1, 2, 3, 4: rho = 4.5 / sqrt(4.5 * 5).
    assert spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.948683, abs=1e-6)


def test_spearman_is_undefined_for_constant_or_tiny_inputs() -> None:
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    assert spearman([1.0], [2.0]) is None
    with pytest.raises(ValueError, match="same length"):
        spearman([1, 2], [1])


def revision_rows() -> tuple[list[ScoredForecast], dict[datetime, float]]:
    """Nine origins; real-time WIS is 2 everywhere, and the hindsight gain grows with origin."""
    rows: list[ScoredForecast] = []
    sizes: dict[datetime, float] = {}
    for i in range(9):
        rows += pair("m", i, 1, 2.0, 2.0 - i / 10)
        sizes[origin(i)] = float(i)
    return rows, sizes


def test_origins_are_grouped_by_revision_size() -> None:
    rows, sizes = revision_rows()
    replicates = make_replicates(9, n_boot=30, block_length=3, seed=0)
    groups, _ = revision_analysis(rows, sizes, replicates=replicates)
    assert [(g.group, g.n_origins) for g in groups] == [(1, 3), (2, 3), (3, 3)]
    assert [(g.revision_low, g.revision_high) for g in groups] == [(0, 2), (3, 5), (6, 8)]
    # Group gaps: hindsight sums are 5.7, 4.8 and 3.9 against 6.0 in real time.
    assert [g.gap for g in groups] == pytest.approx([0.05, 0.2, 0.35])


def test_the_gap_correlates_perfectly_with_revision_size_when_it_is_built_to() -> None:
    rows, sizes = revision_rows()
    replicates = make_replicates(9, n_boot=30, block_length=3, seed=0)
    _, [correlation] = revision_analysis(rows, sizes, replicates=replicates)
    assert correlation.n_origins == 9
    assert correlation.spearman == pytest.approx(1.0)
    assert correlation.low == pytest.approx(1.0)
    assert correlation.high == pytest.approx(1.0)


def test_origins_without_a_revision_size_are_left_out() -> None:
    rows, sizes = revision_rows()
    del sizes[origin(0)], sizes[origin(8)]
    replicates = make_replicates(9, n_boot=10, block_length=3, seed=0)
    groups, [correlation] = revision_analysis(rows, sizes, replicates=replicates)
    assert correlation.n_origins == 7
    assert sum(g.n_origins for g in groups) == 7


def test_no_rows_means_no_revision_analysis() -> None:
    assert revision_analysis([], {}, replicates=[[0]]) == ([], [])


def test_an_origin_drawn_twice_counts_twice_in_a_resample() -> None:
    # Origin 0 has WIS 2 -> 1 and origin 1 has WIS 4 -> 4. Resampling origin 0 twice and origin 1
    # once gives real time 2 + 2 + 4 = 8 and hindsight 1 + 1 + 4 = 6, so the gap is 0.25. If a
    # repeated origin counted once the gap would be 1/6.
    rows = [*pair("m", 0, 1, 2.0, 1.0), *pair("m", 1, 1, 4.0, 4.0)]
    [pooled] = [e for e in analyze(rows, replicates=[[0, 0, 1]]).estimates if e.horizon is None]
    assert pooled.gap_low == pytest.approx(0.25)
    assert pooled.gap_high == pytest.approx(0.25)
    assert pooled.degradation_low == pytest.approx(2.0 / 3.0)  # (8 - 6) over 3 pairs
    assert pooled.gap == pytest.approx(
        1.0 / 6.0
    )  # the point estimate uses each origin once: (6 - 5) / 6
