"""Tests for the replay harness, using the flat series described in replay_fixture.py."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from replay_fixture import (
    EVAL,
    SERIES,
    UTC,
    Constant,
    LastValue,
    config,
    flat_series,
    make_store,
    origin,
    week,
)
from vintage_lakehouse.replay import (
    Forecast,
    Mode,
    ReplayConfig,
    leakage_gap,
    run_replay,
    summarize,
)
from vintage_lakehouse.storage import InMemoryStore, Observation

# --- the headline behaviour ---------------------------------------------------------------


def test_realtime_and_hindsight_scores_match_the_hand_calculation() -> None:
    result = run_replay(make_store(), [LastValue()], config())
    realtime = [r for r in result.rows if r.mode is Mode.REALTIME]
    hindsight = [r for r in result.rows if r.mode is Mode.HINDSIGHT]

    assert realtime and hindsight
    assert all(r.truth == 10.0 for r in result.rows)
    assert all(r.median == 9.0 and r.abs_error == 1.0 for r in realtime)
    assert all(r.median == 10.0 and r.abs_error == 0.0 for r in hindsight)
    assert all(r.wis == pytest.approx(5 / 6) for r in realtime)
    assert all(r.wis == pytest.approx(1 / 6) for r in hindsight)
    assert all(r.coverage == ((0.5, False),) for r in realtime)
    assert all(r.coverage == ((0.5, True),) for r in hindsight)


def test_the_leakage_gap_is_eighty_percent() -> None:
    result = run_replay(make_store(), [LastValue()], config())
    gaps = leakage_gap(result.rows)

    assert [(g.horizon, g.n_pairs) for g in gaps] == [(1, 11), (2, 10), (None, 21)]
    for gap in gaps:
        assert gap.gap == pytest.approx(0.8)
        assert gap.wis_realtime == pytest.approx(5 / 6)
        assert gap.wis_hindsight == pytest.approx(1 / 6)


def test_summary_groups_by_model_horizon_and_mode() -> None:
    result = run_replay(make_store(), [LastValue()], config())
    summaries = summarize(result.rows)

    assert [(s.horizon, s.mode, s.n) for s in summaries] == [
        (1, Mode.HINDSIGHT, 11),
        (1, Mode.REALTIME, 11),
        (2, Mode.HINDSIGHT, 10),
        (2, Mode.REALTIME, 10),
    ]
    by_key = {(s.horizon, s.mode): s for s in summaries}
    assert by_key[(1, Mode.REALTIME)].mean_wis == pytest.approx(5 / 6)
    assert by_key[(1, Mode.HINDSIGHT)].mean_abs_error == 0.0
    assert by_key[(1, Mode.REALTIME)].coverage == ((0.5, 0.0),)
    assert by_key[(1, Mode.HINDSIGHT)].coverage == ((0.5, 1.0),)


def test_targets_without_a_value_at_the_evaluation_time_are_not_scored() -> None:
    result = run_replay(make_store(), [LastValue()], config())
    # Week 10 can only be scored one step ahead (week 11); week 11 has nothing ahead of it.
    by_origin = {o: [r for r in result.rows if r.origin == o] for o in config().origins}
    assert {r.horizon for r in by_origin[origin(10)]} == {1}
    assert by_origin[origin(11)] == []
    assert [s.origin for s in result.skipped] == [origin(11)]
    assert "no target" in result.skipped[0].reason


def test_the_truth_is_the_value_known_at_the_evaluation_time() -> None:
    # At origin(1), week 1 is only provisional (9.0), so that is the "final" value it is scored on.
    result = run_replay(
        make_store(), [LastValue()], config(origins=[origin(0)], evaluation_time=origin(1))
    )
    assert {r.horizon for r in result.rows} == {1}
    assert all(r.truth == 9.0 for r in result.rows)


def test_two_forecasters_are_scored_side_by_side_in_a_stable_order() -> None:
    result = run_replay(make_store(), [LastValue(), Constant("perfect", 10.0)], config())
    first = [(r.model, r.mode) for r in result.rows if r.origin == origin(0) and r.horizon == 1]
    assert first == [
        ("last_value", Mode.REALTIME),
        ("last_value", Mode.HINDSIGHT),
        ("perfect", Mode.REALTIME),
        ("perfect", Mode.HINDSIGHT),
    ]
    gaps = {(g.model, g.horizon): g for g in leakage_gap(result.rows)}
    assert gaps[("last_value", None)].gap == pytest.approx(0.8)
    # A forecaster that is perfect in real time has no real-time error to improve on.
    assert gaps[("perfect", None)].wis_realtime == 0.0
    assert gaps[("perfect", None)].gap is None


# --- information leakage -----------------------------------------------------------------


def test_the_real_time_run_only_sees_what_was_known_at_the_origin() -> None:
    forecaster = LastValue()
    run_replay(make_store(), [forecaster], config(origins=[origin(5)]))
    (realtime_history, _), (hindsight_history, _) = forecaster.calls

    assert max(o.knowledge_time for o in realtime_history) <= origin(5)
    assert realtime_history[-1].event_time == week(5)
    assert realtime_history[-1].value == 9.0  # still provisional
    assert [o.value for o in realtime_history[:-1]] == [10.0] * 5  # earlier weeks are revised

    # The hindsight run sees the same periods, with their settled values, and nothing newer.
    assert [o.event_time for o in hindsight_history] == [o.event_time for o in realtime_history]
    assert [o.value for o in hindsight_history] == [10.0] * 6
    assert max(o.knowledge_time for o in hindsight_history) > origin(5)
    assert max(o.knowledge_time for o in hindsight_history) <= EVAL


def test_forecasters_are_only_asked_about_scorable_targets() -> None:
    forecaster = LastValue()
    run_replay(make_store(), [forecaster], config(origins=[origin(10)]))
    for _, targets in forecaster.calls:
        assert targets == (week(11),)


def test_a_revision_published_after_the_origin_never_reaches_the_real_time_run() -> None:
    forecaster = LastValue()
    run_replay(make_store(), [forecaster], config(origins=[origin(3)]))
    realtime_history = forecaster.calls[0][0]
    assert all(o.knowledge_time <= origin(3) for o in realtime_history)
    assert realtime_history[-1].value == 9.0


Spec = tuple[int, int, int]  # (event day offset 0..9 weeks, hours after event, value)


@settings(deadline=None, max_examples=60)
@given(
    specs=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=9),
            st.integers(min_value=0, max_value=24 * 40),
            st.integers(min_value=-50, max_value=50),
        ),
        unique_by=lambda spec: spec[:2],
        min_size=1,
        max_size=40,
    ),
    origin_hours=st.lists(st.integers(min_value=0, max_value=24 * 120), min_size=1, max_size=4),
)
def test_real_time_never_sees_the_future_and_hindsight_never_sees_unreported_periods(
    specs: list[Spec], origin_hours: list[int]
) -> None:
    observations = [
        Observation(
            SERIES,
            week(w),
            datetime.combine(week(w), time.min, tzinfo=UTC) + timedelta(hours=h),
            float(value),
            "test",
        )
        for w, h, value in specs
    ]
    store = make_store(observations)
    origins = [datetime(2023, 1, 1, tzinfo=UTC) + timedelta(hours=h) for h in origin_hours]
    evaluation_time = datetime(2024, 1, 1, tzinfo=UTC)
    forecaster = LastValue()
    result = run_replay(
        store,
        [forecaster],
        ReplayConfig(SERIES, origins, horizons=[1, 2, 3], evaluation_time=evaluation_time),
    )

    scored_origins = sorted({r.origin for r in result.rows})
    assert len(forecaster.calls) == 2 * len(scored_origins)
    final = {o.event_time: o for o in store.as_of(SERIES, evaluation_time)}
    for index, scored_origin in enumerate(scored_origins):
        realtime_history, _ = forecaster.calls[2 * index]
        hindsight_history, _ = forecaster.calls[2 * index + 1]
        assert list(realtime_history) == store.as_of(SERIES, scored_origin)
        assert all(o.knowledge_time <= scored_origin for o in realtime_history)
        latest = realtime_history[-1].event_time
        assert [o.event_time for o in hindsight_history] == [
            e for e in sorted(final) if e <= latest
        ]
        assert all(final[o.event_time] == o for o in hindsight_history)


# --- determinism ---------------------------------------------------------------------------


def test_replaying_twice_gives_identical_results() -> None:
    store = make_store()
    first = run_replay(store, [LastValue()], config())
    second = run_replay(store, [LastValue()], config())
    assert first.rows == second.rows
    assert first.skipped == second.skipped


def test_the_order_in_which_versions_were_loaded_does_not_matter() -> None:
    observations = flat_series()
    shuffled = list(observations)
    random.Random(7).shuffle(shuffled)
    first = run_replay(make_store(observations), [LastValue()], config())
    second = run_replay(make_store(shuffled), [LastValue()], config())
    assert first.rows == second.rows


def test_the_result_keeps_the_configuration_that_produced_it() -> None:
    cfg = config()
    assert run_replay(make_store(), [LastValue()], cfg).config is cfg


# --- configuration and forecaster errors -------------------------------------------------


def test_evaluation_time_must_not_be_before_the_last_origin() -> None:
    with pytest.raises(ValueError, match="would not be hindsight"):
        config(evaluation_time=origin(5))


def test_times_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="must be timezone-aware"):
        config(origins=[datetime(2023, 3, 1)])
    with pytest.raises(ValueError, match="must be timezone-aware"):
        config(evaluation_time=datetime(2024, 1, 1))


def test_origins_and_horizons_are_sorted_and_deduplicated() -> None:
    cfg = config(origins=[origin(3), origin(1), origin(3)], horizons=[2, 1, 2])
    assert cfg.origins == (origin(1), origin(3))
    assert cfg.horizons == (1, 2)


@pytest.mark.parametrize("horizons", [[], [0], [-1, 2]])
def test_horizons_must_be_positive(horizons: list[int]) -> None:
    with pytest.raises(ValueError, match="horizons must be"):
        config(horizons=horizons)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"step": timedelta(0)}, "step must be positive"),
        ({"min_history": 0}, "min_history"),
        ({"series_id": " "}, "series_id"),
    ],
)
def test_other_configuration_errors(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        config(**overrides)


def test_forecaster_names_must_be_unique() -> None:
    with pytest.raises(ValueError, match="names must be unique"):
        run_replay(make_store(), [LastValue(), LastValue()], config())


def test_no_origins_means_no_results() -> None:
    result = run_replay(make_store(), [LastValue()], config(origins=[]))
    assert result.rows == []
    assert result.skipped == []


def test_origins_with_too_little_history_are_skipped() -> None:
    result = run_replay(
        make_store(), [LastValue()], config(origins=[origin(0), origin(1)], min_history=2)
    )
    assert [s.origin for s in result.skipped] == [origin(0)]
    assert "only 1 periods known" in result.skipped[0].reason
    assert {r.origin for r in result.rows} == {origin(1)}


def test_a_series_with_no_data_is_skipped_everywhere() -> None:
    result = run_replay(InMemoryStore(), [LastValue()], config(origins=[origin(1), origin(2)]))
    assert result.rows == []
    assert len(result.skipped) == 2


def test_a_forecaster_that_omits_a_target_is_an_error() -> None:
    class Forgetful:
        name = "forgetful"

        def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
            return {}

    with pytest.raises(ValueError, match="forgetful returned no forecast"):
        run_replay(make_store(), [Forgetful()], config())


def test_a_forecaster_with_invalid_quantiles_is_named_in_the_error() -> None:
    class Crossing:
        name = "crossing"

        def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
            return {t: {0.25: 5.0, 0.5: 4.0, 0.75: 6.0} for t in targets}

    with pytest.raises(ValueError, match="crossing gave an invalid forecast"):
        run_replay(make_store(), [Crossing()], config())


def test_forecasters_cannot_alter_the_history_they_are_given() -> None:
    class Mutating:
        name = "mutating"

        def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
            assert isinstance(history, tuple)  # immutable, so a forecaster cannot edit it
            return {t: {0.25: 9.0, 0.5: 10.0, 0.75: 11.0} for t in targets}

    run_replay(make_store(), [Mutating()], config(origins=[origin(2)]))


# --- restricting a replay to some modes --------------------------------------------------


def test_a_realtime_only_replay_gives_the_same_realtime_rows_and_no_hindsight_rows() -> None:
    both = run_replay(make_store(), [LastValue()], config())
    realtime_only = run_replay(make_store(), [LastValue()], config(modes=[Mode.REALTIME]))
    assert {r.mode for r in realtime_only.rows} == {Mode.REALTIME}
    assert realtime_only.rows == [r for r in both.rows if r.mode is Mode.REALTIME]


def test_a_hindsight_only_replay_is_possible_too() -> None:
    result = run_replay(make_store(), [LastValue()], config(modes=[Mode.HINDSIGHT]))
    assert result.rows
    assert {r.mode for r in result.rows} == {Mode.HINDSIGHT}


def test_forecasters_are_not_called_for_modes_that_are_not_requested() -> None:
    forecaster = LastValue()
    result = run_replay(make_store(), [forecaster], config(modes=[Mode.REALTIME]))
    assert len(forecaster.calls) == len({r.origin for r in result.rows})


def test_both_modes_are_the_default_and_modes_are_deduplicated() -> None:
    assert config().modes == (Mode.REALTIME, Mode.HINDSIGHT)
    assert config(modes=[Mode.HINDSIGHT, Mode.REALTIME, Mode.REALTIME]).modes == (
        Mode.REALTIME,
        Mode.HINDSIGHT,
    )


def test_at_least_one_mode_is_required() -> None:
    with pytest.raises(ValueError, match="modes"):
        config(modes=[])
