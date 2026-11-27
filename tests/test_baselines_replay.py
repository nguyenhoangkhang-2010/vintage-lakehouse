"""The baselines run inside the replay harness, and react to revisions as expected.

The series repeats one 52-week pattern exactly. Every week is first published 10% too low and
revised to its true value a week later. This gives the baselines very different exposure to the
revision of the latest value:

- SeasonalNaive looks one season back, at values that are long since revised, so its median is
  the same in both runs. The provisional latest value only enters through the spread of its
  past errors, which costs very little.
- ETS extrapolates from the latest values, so a provisional (too low) latest value pulls its
  forecast down and it is much worse in real time than in hindsight.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

import pytest

from series_fixture import START, UTC, seasonal_values
from vintage_lakehouse.forecast import SeasonalNaive
from vintage_lakehouse.replay import Forecaster, Mode, ReplayConfig, leakage_gap, run_replay
from vintage_lakehouse.storage import InMemoryStore, Observation

if TYPE_CHECKING:
    from vintage_lakehouse.forecast.ets import ETSForecaster
    from vintage_lakehouse.forecast.gbm import GradientBoostingForecaster
else:
    # Skips this whole module when the optional baselines dependencies are not installed.
    ETSForecaster = pytest.importorskip("vintage_lakehouse.forecast.ets").ETSForecaster
    GradientBoostingForecaster = pytest.importorskip(
        "vintage_lakehouse.forecast.gbm"
    ).GradientBoostingForecaster

SERIES = "periodic"
N_WEEKS = 160


def at(week: int, extra_days: int) -> datetime:
    return datetime.combine(START + timedelta(weeks=week, days=extra_days), time.min, tzinfo=UTC)


def revised_store() -> InMemoryStore:
    store = InMemoryStore()
    for week, value in enumerate(seasonal_values(N_WEEKS)):
        event = START + timedelta(weeks=week)
        store.append(
            [
                Observation(SERIES, event, at(week, 5), round(value * 0.9, 6), "test"),
                Observation(SERIES, event, at(week, 12), value, "test"),
            ]
        )
    return store


def replay_config() -> ReplayConfig:
    # Just after week w is first published, week w - 1 has already been revised.
    origins = [at(week, 6) for week in range(130, 140)]
    return ReplayConfig(
        series_id=SERIES,
        origins=origins,
        horizons=[1, 2],
        evaluation_time=at(N_WEEKS + 2, 0),
        min_history=100,
    )


def forecasters() -> list[Forecaster]:
    return [
        SeasonalNaive(),
        ETSForecaster(),
        GradientBoostingForecaster(n_estimators=15, max_depth=2),
    ]


def test_every_baseline_scores_every_origin_in_both_modes() -> None:
    result = run_replay(revised_store(), forecasters(), replay_config())
    assert result.skipped == []
    # 10 origins x 2 horizons x 2 modes, for each of the 3 models.
    assert len(result.rows) == 3 * 10 * 2 * 2
    assert {r.model for r in result.rows} == {"seasonal_naive", "ets", "gbm"}
    assert all(r.wis >= 0.0 and r.truth > 0.0 for r in result.rows)


def test_seasonal_naive_forecasts_the_same_median_in_both_modes() -> None:
    result = run_replay(revised_store(), [SeasonalNaive()], replay_config())
    realtime = {(r.origin, r.horizon): r for r in result.rows if r.mode is Mode.REALTIME}
    hindsight = {(r.origin, r.horizon): r for r in result.rows if r.mode is Mode.HINDSIGHT}
    assert realtime.keys() == hindsight.keys()
    assert all(realtime[key].median == hindsight[key].median for key in realtime)

    [pooled] = [g for g in leakage_gap(result.rows) if g.horizon is None]
    # Every seasonal error is zero, so in hindsight the forecast is exact.
    assert pooled.wis_hindsight == pytest.approx(0.0, abs=1e-9)
    # In real time the provisional latest value adds one wrong error to the spread. That is
    # a small effect: orders of magnitude below what ETS suffers in the test below.
    assert pooled.wis_realtime < 0.01


def test_ets_is_worse_in_real_time_than_in_hindsight() -> None:
    result = run_replay(revised_store(), [ETSForecaster()], replay_config())
    [pooled] = [g for g in leakage_gap(result.rows) if g.horizon is None]
    assert pooled.gap is not None
    assert pooled.gap > 0.5
    assert pooled.wis_realtime > 10 * pooled.wis_hindsight


def test_the_replay_of_the_baselines_is_deterministic() -> None:
    first = run_replay(revised_store(), forecasters(), replay_config())
    second = run_replay(revised_store(), forecasters(), replay_config())
    assert first.rows == second.rows
