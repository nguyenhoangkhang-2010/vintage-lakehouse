"""Tests for the experiment configuration, the forecast cache and the runner."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone

import pytest

from experiment_fixture import SERIES, at, revised_store, small_config
from replay_fixture import LastValue
from series_fixture import seasonal_values
from vintage_lakehouse.experiment.runner import (
    CachingForecaster,
    ExperimentConfig,
    Scenario,
    make_forecasters,
    recent_revision_sizes,
    run_analyses,
    run_experiment,
)
from vintage_lakehouse.forecast import SeasonalNaive
from vintage_lakehouse.replay import Forecast, ReplayConfig, run_replay
from vintage_lakehouse.storage import InMemoryStore, Observation

UTC = timezone.utc


# --- configuration -------------------------------------------------------------------------


def test_origins_are_evenly_spaced_and_include_the_last_one_when_it_lines_up() -> None:
    cfg = ExperimentConfig(
        first_origin=datetime(2023, 1, 7, tzinfo=UTC),
        last_origin=datetime(2023, 3, 4, tzinfo=UTC),
        origin_step_weeks=2,
        evaluation_time=datetime(2023, 12, 31, tzinfo=UTC),
    )
    assert [o.strftime("%m-%d") for o in cfg.origins()] == [
        "01-07",
        "01-21",
        "02-04",
        "02-18",
        "03-04",
    ]


def test_origins_stop_at_the_last_one_that_fits() -> None:
    cfg = ExperimentConfig(
        first_origin=datetime(2023, 1, 7, tzinfo=UTC),
        last_origin=datetime(2023, 3, 1, tzinfo=UTC),
        origin_step_weeks=2,
        evaluation_time=datetime(2023, 12, 31, tzinfo=UTC),
    )
    assert cfg.origins()[-1] == datetime(2023, 2, 18, tzinfo=UTC)


def test_evaluation_times_are_the_primary_one_and_earlier_sensitivity_times() -> None:
    cfg = ExperimentConfig(
        first_origin=datetime(2023, 1, 7, tzinfo=UTC),
        last_origin=datetime(2023, 3, 4, tzinfo=UTC),
        origin_step_weeks=2,
        evaluation_time=datetime(2023, 12, 31, tzinfo=UTC),
        sensitivity_weeks=(13, 26, 52),
    )
    # 52 weeks after the last origin is after the primary time, so it is dropped.
    assert [t.strftime("%Y-%m-%d") for t in cfg.evaluation_times()] == [
        "2023-06-03",
        "2023-09-02",
        "2023-12-31",
    ]


@pytest.mark.parametrize(("step", "expected"), [(4, 3), (5, 3), (1, 12), (20, 1), (12, 1)])
def test_the_block_length_covers_the_block_in_weeks(step: int, expected: int) -> None:
    assert small_config(origin_step_weeks=step, block_weeks=12).block_length() == expected


def test_scenarios_pair_each_evaluation_time_with_and_without_the_exclusions() -> None:
    cfg = small_config()
    scenarios = cfg.scenarios()
    assert len(scenarios) == 2 * len(cfg.evaluation_times())
    assert scenarios[0].excluded == ()
    assert scenarios[1].excluded == cfg.exclusions
    assert cfg.primary == Scenario(cfg.evaluation_time)
    assert "all origins" in cfg.primary.label
    assert "excluding origins" in scenarios[1].label


def test_without_exclusions_there_is_one_scenario_per_evaluation_time() -> None:
    cfg = small_config(exclusions=())
    assert len(cfg.scenarios()) == len(cfg.evaluation_times())


def test_a_scenario_drops_origins_inside_an_excluded_span() -> None:
    scenario = Scenario(at(162), ((at(133), at(135)),))
    assert not scenario.keeps(at(133, 6))
    assert scenario.keeps(at(136, 6))
    assert scenario.keeps(at(130, 6))


@pytest.mark.parametrize(
    "overrides",
    [
        {"models": ()},
        {"models": ("seasonal_naive", "seasonal_naive")},
        {"models": ("prophet",)},
        {"horizons": ()},
        {"horizons": (0, 1)},
        {"confidence": 1.0},
        {"confidence": 0.0},
        {"n_boot": 0},
        {"block_weeks": 0},
        {"origin_step_weeks": 0},
        {"sensitivity_weeks": (0,)},
        {"first_origin": at(140), "last_origin": at(139)},
        {"evaluation_time": at(100)},
        {"exclusions": ((at(135), at(133)),)},
        {"first_origin": datetime(2023, 1, 1)},
    ],
)
def test_invalid_configurations_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        small_config(**overrides)


def test_horizons_are_sorted_and_deduplicated() -> None:
    assert small_config(horizons=(3, 1, 3, 2)).horizons == (1, 2, 3)


# --- building models -----------------------------------------------------------------------


def test_the_lower_bound_reaches_the_models() -> None:
    [model] = make_forecasters(["seasonal_naive"], lower_bound=0.0)
    assert isinstance(model, SeasonalNaive)
    assert model.lower_bound == 0.0


def test_unknown_models_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        make_forecasters(["magic"], lower_bound=None)


def test_the_optional_models_are_built_when_their_extras_are_installed() -> None:
    pytest.importorskip("vintage_lakehouse.forecast.ets")
    pytest.importorskip("vintage_lakehouse.forecast.gbm")
    models = make_forecasters(["ets", "gbm"], lower_bound=0.0)
    assert [m.name for m in models] == ["ets", "gbm"]


# --- the forecast cache --------------------------------------------------------------------


class Counting:
    """Forecasts a constant and records how many targets it was asked about."""

    name = "counting"

    def __init__(self) -> None:
        self.asked: list[tuple[date, ...]] = []

    def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
        self.asked.append(tuple(targets))
        return {t: {0.25: 1.0, 0.5: 2.0, 0.75: 3.0} for t in targets}


def history(n: int = 3) -> list[Observation]:
    return revised_store().as_of(SERIES, at(n, 6))


def test_the_same_history_and_targets_are_only_forecast_once() -> None:
    inner = Counting()
    cached = CachingForecaster(inner)
    h = history()
    targets = [date(2024, 1, 7), date(2024, 1, 14)]
    first = cached.forecast(h, targets)
    again = cached.forecast(list(h), targets)
    assert first == again
    assert len(inner.asked) == 1
    assert (cached.requested, cached.computed) == (4, 2)


def test_only_the_missing_targets_are_forecast() -> None:
    inner = Counting()
    cached = CachingForecaster(inner)
    h = history()
    cached.forecast(h, [date(2024, 1, 7)])
    cached.forecast(h, [date(2024, 1, 7), date(2024, 1, 14)])
    assert inner.asked == [(date(2024, 1, 7),), (date(2024, 1, 14),)]


def test_a_different_history_is_forecast_again() -> None:
    inner = Counting()
    cached = CachingForecaster(inner)
    cached.forecast(history(3), [date(2024, 1, 7)])
    cached.forecast(history(4), [date(2024, 1, 7)])
    assert len(inner.asked) == 2


def test_cached_forecasts_cannot_be_altered_by_the_caller() -> None:
    cached = CachingForecaster(Counting())
    h = history()
    target = date(2024, 1, 7)
    handed_out = cached.forecast(h, [target])[target]
    assert isinstance(handed_out, dict)
    handed_out[0.5] = 999.0
    assert cached.forecast(h, [target])[target][0.5] == 2.0


def test_a_forecaster_that_omits_a_target_is_reported_by_name() -> None:
    class Forgetful:
        name = "forgetful"

        def forecast(self, history: Sequence[Observation], targets: Sequence[date]) -> Forecast:
            return {}

    with pytest.raises(ValueError, match="forgetful returned no forecast"):
        CachingForecaster(Forgetful()).forecast(history(), [date(2024, 1, 7)])


# --- running -------------------------------------------------------------------------------


def test_caching_does_not_change_any_score() -> None:
    cfg = small_config()
    result = run_experiment(revised_store(), [SeasonalNaive(), LastValue()], cfg)

    assert list(result.rows_by_eval) == cfg.evaluation_times()  # ascending
    for evaluation_time, rows in result.rows_by_eval.items():
        direct = run_replay(
            revised_store(),
            [SeasonalNaive(), LastValue()],
            ReplayConfig(
                series_id=SERIES,
                origins=cfg.origins(),
                horizons=cfg.horizons,
                evaluation_time=evaluation_time,
                min_history=SeasonalNaive().min_history,
            ),
        )
        assert rows == direct.rows
        assert result.skipped_by_eval[evaluation_time] == direct.skipped


def test_real_time_forecasts_are_computed_once_across_evaluation_times() -> None:
    result = run_experiment(revised_store(), [SeasonalNaive()], small_config())
    assert result.cache_computed["seasonal_naive"] < result.cache_requested["seasonal_naive"]


def test_progress_is_reported_for_each_evaluation_time() -> None:
    messages: list[str] = []
    cfg = small_config()
    run_experiment(revised_store(), [SeasonalNaive()], cfg, progress=messages.append)
    assert sum("replaying with T_eval" in m for m in messages) == len(cfg.evaluation_times())
    assert any("done in" in m for m in messages)


def test_the_recent_revision_size_is_worked_out_by_hand() -> None:
    cfg = small_config()
    sizes = recent_revision_sizes(revised_store(), SERIES, cfg.origins(), cfg.evaluation_time, 4)
    values = seasonal_values(160)
    for week in (130, 133, 136, 139):
        # Of the last 4 weeks known at the origin only the latest is still provisional (10% low).
        assert sizes[at(week, 6)] == pytest.approx(0.1 * values[week] / 4, rel=1e-4)


def test_origins_without_any_known_period_have_no_revision_size() -> None:
    sizes = recent_revision_sizes(InMemoryStore(), SERIES, [at(5, 6)], at(162), 4)
    assert sizes == {}


def test_the_revision_window_changes_the_average() -> None:
    cfg = small_config()
    two = recent_revision_sizes(revised_store(), SERIES, cfg.origins(), cfg.evaluation_time, 2)
    four = recent_revision_sizes(revised_store(), SERIES, cfg.origins(), cfg.evaluation_time, 4)
    origin = at(130, 6)
    assert two[origin] == pytest.approx(2 * four[origin])


def test_origins_with_too_little_history_are_skipped_and_do_not_break_the_run() -> None:
    cfg = small_config(first_origin=at(10, 6), last_origin=at(19, 6))
    result = run_experiment(revised_store(), [SeasonalNaive()], cfg)
    assert all(rows == [] for rows in result.rows_by_eval.values())
    assert all(len(s) == len(cfg.origins()) for s in result.skipped_by_eval.values())
    analyses = run_analyses(result)
    assert all(a.n_origins == 0 for a in analyses.by_scenario.values())
    assert analyses.revision_groups == []


# --- the analyses --------------------------------------------------------------------------


def test_every_scenario_is_analysed_and_the_exclusion_removes_an_origin() -> None:
    cfg = small_config()
    result = run_experiment(revised_store(), [SeasonalNaive(), LastValue()], cfg)
    analyses = run_analyses(result)
    assert set(analyses.by_scenario) == set(cfg.scenarios())
    everything = analyses.by_scenario[cfg.primary]
    without = analyses.by_scenario[Scenario(cfg.evaluation_time, cfg.exclusions)]
    assert everything.n_origins == 4
    assert without.n_origins == 3
    assert {e.model for e in everything.estimates} == {"seasonal_naive", "last_value"}


def test_last_value_is_far_more_exposed_to_revisions_than_seasonal_naive() -> None:
    cfg = small_config()
    result = run_experiment(revised_store(), [SeasonalNaive(), LastValue()], cfg)
    pooled = {
        e.model: e
        for e in run_analyses(result).by_scenario[cfg.primary].estimates
        if e.horizon is None
    }
    # The sign can go either way: a first release that is 10% low can accidentally pull a
    # last-value forecast towards a falling series. What matters is the size of the effect.
    assert abs(pooled["last_value"].degradation) > 10 * abs(pooled["seasonal_naive"].degradation)


def test_the_revision_analysis_uses_the_primary_evaluation_time() -> None:
    cfg = small_config()
    analyses = run_analyses(run_experiment(revised_store(), [SeasonalNaive()], cfg))
    assert [c.model for c in analyses.revision_correlations] == ["seasonal_naive"]
    assert analyses.revision_correlations[0].n_origins == 4


def test_the_whole_experiment_is_deterministic() -> None:
    cfg = small_config()
    first = run_experiment(revised_store(), [SeasonalNaive(), LastValue()], cfg)
    second = run_experiment(revised_store(), [SeasonalNaive(), LastValue()], cfg)
    assert first.rows_by_eval == second.rows_by_eval
    assert run_analyses(first) == run_analyses(second)


def test_origin_spacing_in_the_small_configuration() -> None:
    cfg = small_config()
    assert cfg.origins() == [at(w, 6) for w in (130, 133, 136, 139)]
    assert cfg.evaluation_times() == [
        at(139, 6) + timedelta(weeks=4),
        at(139, 6) + timedelta(weeks=8),
        at(162),
    ]


def test_origins_exactly_on_the_edges_of_an_excluded_span_are_dropped() -> None:
    scenario = Scenario(at(162), ((at(133), at(135)),))
    assert not scenario.keeps(at(133))
    assert not scenario.keeps(at(135))
    assert scenario.keeps(at(133) - timedelta(seconds=1))
    assert scenario.keeps(at(135) + timedelta(seconds=1))
