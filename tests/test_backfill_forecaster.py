"""Tests for the backfill-corrected forecaster wrapper."""

from __future__ import annotations

from datetime import timedelta

import pytest

from backfill_fixture import (
    SERIES,
    make_store,
    origin,
    report_time,
    revised_series,
    varying_truth,
    week,
)
from replay_fixture import Constant, LastValue
from vintage_lakehouse.backfill import BackfillCorrected, CorrectionConfig, VintageTable
from vintage_lakehouse.replay import Mode, ReplayConfig, run_replay
from vintage_lakehouse.storage import Observation

N = 30
LAST = 20


def cfg(**overrides: object) -> CorrectionConfig:
    fields: dict[str, object] = {"settle": 3, "max_lag": 3, "min_samples": 5}
    fields.update(overrides)
    return CorrectionConfig(**fields)  # type: ignore[arg-type]


def corrected_history(
    observations: list[Observation],
    at_week: int = LAST,
    config: CorrectionConfig | None = None,
    table_observations: list[Observation] | None = None,
) -> tuple[list[Observation], tuple[Observation, ...]]:
    """The history as seen at a week, and the history the base forecaster was given."""
    history = make_store(observations).as_of(SERIES, origin(at_week))
    base = LastValue()
    table = VintageTable.from_observations(table_observations or observations)
    wrapper = BackfillCorrected(base, table, config or cfg())
    target = week(at_week + 1)
    wrapper.forecast(history, [target])
    return history, base.calls[0][0]


def test_corrected_history_equals_the_settled_history_on_a_constant_bias() -> None:
    observations = revised_series(N, varying_truth)
    history, adjusted = corrected_history(observations)
    # at the origin week LAST is at lag 0 (80% of truth), LAST - 1 at lag 1 (90%), the rest final
    assert history[-1].value == pytest.approx(0.8 * varying_truth(LAST))
    assert [o.value for o in adjusted] == pytest.approx([varying_truth(o) for o in range(LAST + 1)])


def test_only_values_change_not_periods_or_knowledge_times() -> None:
    history, adjusted = corrected_history(revised_series(N, varying_truth))
    assert [(o.event_time, o.knowledge_time, o.source) for o in adjusted] == [
        (o.event_time, o.knowledge_time, o.source) for o in history
    ]
    assert len(adjusted) == len(history) == LAST + 1


def test_values_beyond_max_lag_are_untouched() -> None:
    history, adjusted = corrected_history(revised_series(N, varying_truth))
    for original, new in zip(history[: LAST + 1 - 3], adjusted[: LAST + 1 - 3], strict=True):
        assert new.value == original.value


def test_without_revisions_the_forecast_is_the_base_forecast() -> None:
    observations = revised_series(N, varying_truth, factors=(1.0,))
    history, adjusted = corrected_history(observations)
    assert tuple(history) == adjusted


def test_the_forecast_is_the_base_forecasters_on_the_adjusted_history() -> None:
    observations = revised_series(N, varying_truth)
    history = make_store(observations).as_of(SERIES, origin(LAST))
    wrapper = BackfillCorrected(LastValue(), VintageTable.from_observations(observations), cfg())
    targets = [week(LAST + 1), week(LAST + 2)]
    forecast = wrapper.forecast(history, targets)
    expected_median = varying_truth(LAST)  # last value after correction = the settled value
    for target in targets:
        assert forecast[target][0.5] == pytest.approx(expected_median)


def test_gaps_in_the_history_do_not_shift_the_lags() -> None:
    observations = revised_series(N, varying_truth)
    history = make_store(observations).as_of(SERIES, origin(LAST))
    without_week_19 = [o for o in history if o.event_time != week(LAST - 1)]
    base = LastValue()
    wrapper = BackfillCorrected(base, VintageTable.from_observations(observations), cfg())
    wrapper.forecast(without_week_19, [week(LAST + 1)])
    adjusted = base.calls[0][0]
    assert adjusted[-1].value == pytest.approx(varying_truth(LAST))  # still lag 0
    assert adjusted[-2].event_time == week(LAST - 2)
    assert adjusted[-2].value == history[-3].value  # lag 2: not corrected


def test_an_observation_that_is_not_a_whole_number_of_steps_back_is_left_alone() -> None:
    observations = revised_series(N, varying_truth)
    history = make_store(observations).as_of(SERIES, origin(LAST))
    odd = Observation(SERIES, week(LAST - 1) + timedelta(days=4), report_time(LAST - 1), 7.0, "t")
    mixed = [*history[:-1], odd, history[-1]]  # three days before the latest period
    base = LastValue()
    BackfillCorrected(base, VintageTable.from_observations(observations), cfg()).forecast(
        mixed, [week(LAST + 1)]
    )
    adjusted = base.calls[0][0]
    assert adjusted[-2] == odd
    assert adjusted[-1].value == pytest.approx(varying_truth(LAST))


def test_the_name_says_what_it_wraps_and_can_be_overridden() -> None:
    table = VintageTable.from_observations(revised_series(N, varying_truth))
    assert BackfillCorrected(LastValue(), table, cfg()).name == "last_value+backfill"
    assert BackfillCorrected(Constant("c", 1.0), table, cfg(), name="placebo").name == "placebo"


def test_min_history_is_taken_from_the_base_forecaster() -> None:
    table = VintageTable.from_observations(revised_series(N, varying_truth))

    class WithMinimum(LastValue):
        min_history = 52

    assert BackfillCorrected(WithMinimum(), table, cfg()).min_history == 52
    assert BackfillCorrected(LastValue(), table, cfg()).min_history == 1


def test_an_empty_history_is_passed_through_to_the_base_forecaster() -> None:
    table = VintageTable.from_observations(revised_series(N, varying_truth))
    wrapper = BackfillCorrected(Constant("c", 3.0), table, cfg())
    assert wrapper.forecast([], [week(1)])[week(1)][0.5] == 3.0


# --- no look-ahead -----------------------------------------------------------------------


def test_future_versions_in_the_table_do_not_change_what_the_base_forecaster_sees() -> None:
    clean = revised_series(N, varying_truth)
    poisoned = [
        *clean,
        *[
            Observation(SERIES, week(w), report_time(LAST + 3), 1e6, "test")
            for w in range(LAST - 2)
        ],
        Observation(SERIES, week(LAST), report_time(LAST + 4), 1e6, "test"),
        Observation(SERIES, week(LAST + 5), report_time(LAST + 5), 1e6, "test"),
    ]
    history = make_store(clean).as_of(SERIES, origin(LAST))
    results = []
    for table_observations in (clean, poisoned):
        base = LastValue()
        table = VintageTable.from_observations(table_observations)
        BackfillCorrected(base, table, cfg()).forecast(history, [week(LAST + 1)])
        results.append(base.calls[0][0])
    assert results[0] == results[1]


def test_an_early_origin_does_not_use_later_revision_patterns() -> None:
    def factors_for(w: int) -> tuple[float, ...]:
        return (0.8, 1.0) if w < 12 else (0.5, 1.0)

    observations = revised_series(N, [10.0] * N, factors=factors_for)
    config = cfg(max_lag=2, settle=2)
    _, early = corrected_history(observations, at_week=11, config=config)
    truncated = [o for o in observations if o.knowledge_time <= origin(11)]
    _, from_truncated = corrected_history(truncated, at_week=11, config=config)
    assert early == from_truncated
    assert early[-1].value == pytest.approx(10.0)  # 8.0 * 1.25


# --- inside the replay harness -----------------------------------------------------------


def test_replay_with_the_correction_matches_hindsight_when_the_bias_is_constant() -> None:
    """Flat truth of 10, first release 9: the base model scores 5/6 in real time and 1/6 in
    hindsight (replay_fixture); the corrected model fed the settled value must score 1/6."""
    n = 30
    observations = revised_series(n, [10.0] * n, factors=(0.9, 1.0))
    store = make_store(observations)
    table = VintageTable.from_store(store, SERIES, report_time(n + 5))
    config = ReplayConfig(
        series_id=SERIES,
        origins=[origin(w) for w in range(12, 27)],
        horizons=[1],
        evaluation_time=report_time(n + 5),
    )
    corrected = BackfillCorrected(LastValue(), table, cfg(max_lag=2), name="last_value+backfill")
    result = run_replay(store, [LastValue(), corrected], config)

    def mean_wis(model: str, mode: Mode) -> float:
        rows = [r.wis for r in result.rows if r.model == model and r.mode is mode]
        assert rows
        return sum(rows) / len(rows)

    assert mean_wis("last_value", Mode.REALTIME) == pytest.approx(5 / 6)
    assert mean_wis("last_value", Mode.HINDSIGHT) == pytest.approx(1 / 6)
    assert mean_wis("last_value+backfill", Mode.REALTIME) == pytest.approx(1 / 6)
