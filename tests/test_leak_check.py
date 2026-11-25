"""Tests for the dynamic look-ahead check, with seeded leaks in test pipelines."""

from __future__ import annotations

import random
import statistics
from datetime import datetime

import pytest

from backfill_fixture import (
    SERIES,
    UTC,
    make_store,
    origin,
    report_time,
    revised_series,
    varying_truth,
)
from vintage_lakehouse.audit import FAR_FUTURE, check_pipeline
from vintage_lakehouse.audit.leak_check import LeakCheckResult, OriginCheck, Pipeline
from vintage_lakehouse.storage import AsOfStore

N = 40
ORIGINS = [origin(w) for w in (15, 20, 25)]


def store() -> AsOfStore:
    return make_store(revised_series(N, varying_truth))


# --- pipelines: one clean, several with a seeded leak ------------------------------------


def clean_last_value(s: AsOfStore, series: str, at: datetime) -> float:
    return s.as_of(series, at)[-1].value


def clean_history_mean(s: AsOfStore, series: str, at: datetime) -> float:
    return statistics.fmean(o.value for o in s.as_of(series, at))


def clean_with_manual_filter(s: AsOfStore, series: str, at: datetime) -> list[float]:
    """Reads every version but keeps only those published by the origin: correct."""
    latest = s.as_of(series, at)[-1]
    versions = [v for v in s.revisions(series, latest.event_time) if v.knowledge_time <= at]
    return [v.value for v in versions]


def leaks_snapshot(s: AsOfStore, series: str, at: datetime) -> float:
    """Takes the latest value as of a far-future time: a snapshot of today's data."""
    return s.as_of(series, FAR_FUTURE)[-1].value


def leaks_revisions(s: AsOfStore, series: str, at: datetime) -> float:
    """Uses the settled value of every week known at the origin."""
    known = s.as_of(series, at)
    final = {o.event_time: o.value for o in s.as_of(series, FAR_FUTURE)}
    return statistics.fmean(final[o.event_time] for o in known)


def leaks_last_revision(s: AsOfStore, series: str, at: datetime) -> float:
    """Takes the last stored version of the latest week, whenever it was published."""
    latest = s.as_of(series, at)[-1]
    return s.revisions(series, latest.event_time)[-1].value


def leaks_future_period(s: AsOfStore, series: str, at: datetime) -> float:
    """Uses the value of the week after the origin's latest week."""
    known = s.as_of(series, at)
    everything = s.as_of(series, FAR_FUTURE)
    return everything[len(known)].value


# --- the check ---------------------------------------------------------------------------


def run(pipeline: Pipeline) -> LeakCheckResult:
    return check_pipeline(store(), SERIES, pipeline, ORIGINS)


@pytest.mark.parametrize(
    "pipeline", [clean_last_value, clean_history_mean, clean_with_manual_filter]
)
def test_clean_pipelines_pass_at_every_origin(pipeline: Pipeline) -> None:
    result = run(pipeline)
    assert [c.status for c in result.origins] == ["clean"] * 3
    assert result.ok and not result.leaks


@pytest.mark.parametrize(
    "pipeline", [leaks_snapshot, leaks_revisions, leaks_last_revision, leaks_future_period]
)
def test_each_seeded_leak_is_found_at_every_origin(pipeline: Pipeline) -> None:
    result = run(pipeline)
    assert [c.status for c in result.origins] == ["leak"] * 3
    assert result.leaks and not result.ok
    assert all(c.max_difference > 0.0 for c in result.origins)


def test_a_version_published_exactly_at_the_origin_is_known_at_the_origin() -> None:
    exact = report_time(20)  # the moment report 20 is published: as_of includes it
    result = check_pipeline(store(), SERIES, clean_last_value, [exact])
    assert [c.status for c in result.origins] == ["clean"]
    leaky = check_pipeline(store(), SERIES, leaks_snapshot, [exact])
    assert [c.status for c in leaky.origins] == ["leak"]


def test_one_failing_origin_means_the_pipeline_is_not_proven_clean() -> None:
    def fails_once(s: AsOfStore, series: str, at: datetime) -> float:
        if at == ORIGINS[1]:
            raise ValueError("bad origin")
        return s.as_of(series, at)[-1].value

    result = run(fails_once)
    assert [c.status for c in result.origins] == ["clean", "error", "clean"]
    assert not result.ok


def test_the_difference_is_reported() -> None:
    result = run(leaks_snapshot)
    assert all(c.max_difference > 1e-6 for c in result.origins)
    assert {c.origin for c in result.origins} == set(ORIGINS)


def test_an_origin_with_no_later_data_is_inconclusive_never_clean() -> None:
    late = origin(N - 1)  # after the last report: nothing to alter
    result = check_pipeline(store(), SERIES, clean_last_value, [late])
    assert [c.status for c in result.origins] == ["inconclusive"]
    assert not result.ok  # nothing was proven
    leaky = check_pipeline(store(), SERIES, leaks_snapshot, [late])
    assert [c.status for c in leaky.origins] == ["inconclusive"]


def test_a_pipeline_that_fails_without_future_data_is_a_leak() -> None:
    def needs_the_future(s: AsOfStore, series: str, at: datetime) -> float:
        everything = s.as_of(series, FAR_FUTURE)
        return everything[30].value  # exists only while the later weeks are present

    result = run(needs_the_future)
    assert all(c.status == "leak" for c in result.origins)
    assert any("without" in c.detail.lower() or "error" in c.detail.lower() for c in result.origins)


def test_a_pipeline_that_fails_on_real_data_is_reported_not_swallowed() -> None:
    def broken(s: AsOfStore, series: str, at: datetime) -> float:
        raise RuntimeError("boom")

    result = run(broken)
    assert [c.status for c in result.origins] == ["error"] * 3
    assert "boom" in result.origins[0].detail
    assert not result.ok


def test_a_nondeterministic_pipeline_cannot_be_checked() -> None:
    rng = random.Random(1)

    def noisy(s: AsOfStore, series: str, at: datetime) -> float:
        return rng.random()

    result = run(noisy)
    assert [c.status for c in result.origins] == ["nondeterministic"] * 3
    assert not result.ok


def test_results_can_be_numbers_mappings_or_sequences() -> None:
    def mapping(s: AsOfStore, series: str, at: datetime) -> dict[str, float]:
        known = s.as_of(series, at)
        return {"last": known[-1].value, "first": known[0].value}

    def sequence(s: AsOfStore, series: str, at: datetime) -> list[float]:
        return [o.value for o in s.as_of(series, at)[-3:]]

    assert run(mapping).ok and run(sequence).ok

    def leaky_mapping(s: AsOfStore, series: str, at: datetime) -> dict[str, float]:
        return {"ok": s.as_of(series, at)[-1].value, "bad": leaks_snapshot(s, series, at)}

    assert run(leaky_mapping).leaks


def test_non_numeric_results_are_compared_for_equality() -> None:
    def label(s: AsOfStore, series: str, at: datetime) -> str:
        return "up" if s.as_of(series, at)[-1].value > 0 else "down"

    def leaky_label(s: AsOfStore, series: str, at: datetime) -> str:
        return "up" if s.as_of(series, FAR_FUTURE)[-1].value > 7.5 else "down"

    assert run(label).ok
    # the label depends on the last week of the whole series, which the alteration changes
    assert run(leaky_label).leaks


def test_the_store_given_to_the_check_is_not_modified() -> None:
    s = store()
    before = {o.event_time: o.value for o in s.as_of(SERIES, FAR_FUTURE)}
    check_pipeline(s, SERIES, leaks_snapshot, ORIGINS)
    after = {o.event_time: o.value for o in s.as_of(SERIES, FAR_FUTURE)}
    assert before == after


def test_both_alterations_are_applied_and_a_leak_in_either_counts() -> None:
    def breaks_only_when_removed(s: AsOfStore, series: str, at: datetime) -> float:
        # unchanged when later values are scaled (compares to a threshold), but the week
        # disappears when later versions are dropped
        return float(len(s.as_of(series, FAR_FUTURE)) > 35)

    result = run(breaks_only_when_removed)
    assert all(c.status == "leak" for c in result.origins)


def test_an_unknown_series_is_inconclusive() -> None:
    result = check_pipeline(store(), "no/such", clean_last_value, ORIGINS)
    assert [c.status for c in result.origins] == ["inconclusive"] * 3


def test_origins_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        check_pipeline(store(), SERIES, clean_last_value, [datetime(2023, 6, 1)])


def test_no_origins_give_an_empty_result() -> None:
    result = check_pipeline(store(), SERIES, clean_last_value, [])
    assert result.origins == [] and not result.ok


def test_summary_text_names_the_leaking_origins() -> None:
    result = run(leaks_snapshot)
    text = result.summary()
    assert "LEAK" in text and ORIGINS[0].date().isoformat() in text
    assert isinstance(result.origins[0], OriginCheck)
    assert "clean" in run(clean_last_value).summary().lower()
    assert UTC is not None
