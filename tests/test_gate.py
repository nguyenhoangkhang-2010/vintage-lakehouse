"""Tests for the week-0 gate evaluation logic (no network)."""

from __future__ import annotations

from datetime import date, timedelta

from vintage_lakehouse.ingestion.gate import (
    CriterionResult,
    ProbeMeta,
    Status,
    VersionedPoint,
    compute_stats,
    evaluate,
    format_markdown,
)

META_OK = ProbeMeta(requests=14, elapsed_seconds=10.0, auth_required=False, throttled=False)


def make_points(
    *,
    n_events: int = 10,
    versions: int = 5,
    revise: bool = True,
    step_days: int = 200,
) -> list[VersionedPoint]:
    points: list[VersionedPoint] = []
    for i in range(n_events):
        event = date(2019, 1, 6) + timedelta(days=step_days * i)
        for v in range(versions):
            points.append(
                VersionedPoint(
                    event_time=event,
                    knowledge_time=event + timedelta(days=7 * (v + 1)),
                    value=1.0 + (0.1 * v if revise else 0.0),
                )
            )
    return points


def status_of(criteria: tuple[CriterionResult, ...], number: int) -> Status:
    return next(c.status for c in criteria if c.number == number)


def test_good_source_passes_with_manual_items_left() -> None:
    report = evaluate("good", make_points(), META_OK)
    assert report.passed
    assert [status_of(report.criteria, n) for n in (1, 2, 3, 5)] == [Status.PASS] * 4
    assert status_of(report.criteria, 4) is Status.MANUAL
    assert [c.number for c in report.manual] == [4]


def test_too_few_versions_fails_criterion_one() -> None:
    report = evaluate("few", make_points(versions=2), META_OK)
    assert status_of(report.criteria, 1) is Status.FAIL
    assert not report.passed


def test_values_that_never_change_fail_criterion_one() -> None:
    report = evaluate("static", make_points(revise=False), META_OK)
    assert report.stats.revised_share == 0.0
    assert status_of(report.criteria, 1) is Status.FAIL


def test_short_history_fails_criterion_two() -> None:
    report = evaluate("short", make_points(step_days=7), META_OK)
    assert status_of(report.criteria, 2) is Status.FAIL


def test_empty_input_fails_data_criteria() -> None:
    report = evaluate("empty", [], META_OK)
    assert report.stats.n_event_times == 0
    assert status_of(report.criteria, 1) is Status.FAIL
    assert status_of(report.criteria, 2) is Status.FAIL


def test_credentials_need_a_human_decision() -> None:
    meta = ProbeMeta(requests=1, elapsed_seconds=1.0, auth_required=True, throttled=False)
    report = evaluate("keyed", make_points(), meta)
    assert status_of(report.criteria, 3) is Status.MANUAL
    assert report.passed


def test_throttling_fails_criterion_five() -> None:
    meta = ProbeMeta(requests=5, elapsed_seconds=2.0, auth_required=False, throttled=True)
    report = evaluate("throttled", make_points(), meta)
    assert status_of(report.criteria, 5) is Status.FAIL


def test_compute_stats_handles_unequal_version_counts() -> None:
    jan = date(2020, 1, 5)
    feb = date(2020, 2, 2)
    points = [
        VersionedPoint(jan, jan + timedelta(days=7), 1.0),
        VersionedPoint(jan, jan + timedelta(days=14), 1.5),
        VersionedPoint(jan, jan + timedelta(days=21), 1.5),
        VersionedPoint(feb, feb + timedelta(days=7), 2.0),
    ]
    stats = compute_stats(points)
    assert stats.n_event_times == 2
    assert stats.n_rows == 4
    assert stats.min_versions == 1
    assert stats.median_versions == 2.0
    assert stats.revised_share == 0.5
    assert stats.first_event == jan
    assert stats.last_event == feb


def test_markdown_lists_every_criterion() -> None:
    text = format_markdown(evaluate("my-source", make_points(), META_OK))
    assert "### Gate report: my-source" in text
    for number in range(1, 6):
        assert f"| {number} |" in text
