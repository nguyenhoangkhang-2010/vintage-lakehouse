"""Reusable contract tests for any ``AsOfStore`` implementation.

To test a new store, subclass ``StoreContract`` in a ``test_*.py`` module and implement
``make_store``. The in-memory reference store and the Iceberg-backed store run the same tests.
A store that is slow to create can lower ``property_examples``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from vintage_lakehouse.storage import AsOfStore, ConflictError, Observation

UTC = timezone.utc
VIETNAM = timezone(timedelta(hours=7))

SERIES = "fluview"
EVENT = date(2023, 1, 1)
FIRST = datetime(2023, 1, 14, tzinfo=UTC)
SECOND = datetime(2023, 1, 21, tzinfo=UTC)
THIRD = datetime(2023, 1, 28, tzinfo=UTC)


def observation(
    *,
    series: str = SERIES,
    event: date = EVENT,
    known: datetime = FIRST,
    value: float = 4.0,
    source: str = "test",
    ingest: datetime | None = None,
) -> Observation:
    return Observation(
        series_id=series,
        event_time=event,
        knowledge_time=known,
        value=value,
        source=source,
        ingest_time=ingest,
    )


def three_versions() -> list[Observation]:
    """One week published three times, with its value revised each time."""
    return [
        observation(known=FIRST, value=4.00),
        observation(known=SECOND, value=4.10),
        observation(known=THIRD, value=4.05),
    ]


# --- helpers for the property tests -----------------------------------------------------

BASE = datetime(2023, 1, 1, tzinfo=UTC)
SERIES_IDS = ["alpha", "beta"]
Spec = tuple[str, int, int, int]  # (series, event day offset, knowledge hours, value)

version_specs = st.lists(
    st.tuples(
        st.sampled_from(SERIES_IDS),
        st.integers(min_value=0, max_value=4),
        st.integers(min_value=0, max_value=24 * 20),
        st.integers(min_value=-1000, max_value=1000),
    ),
    unique_by=lambda spec: spec[:3],
    max_size=40,
)
query_hours = st.integers(min_value=-48, max_value=24 * 40)


def build(specs: Sequence[Spec]) -> list[Observation]:
    observations: list[Observation] = []
    for series, event_offset, known_hours, value in specs:
        event = BASE.date() + timedelta(days=event_offset)
        known = datetime.combine(event, time.min, tzinfo=UTC) + timedelta(hours=known_hours)
        observations.append(
            observation(series=series, event=event, known=known, value=float(value))
        )
    return observations


def at_hours(hours: int) -> datetime:
    return BASE + timedelta(hours=hours)


def oracle(observations: Sequence[Observation], series_id: str, at: datetime) -> list[Observation]:
    """Obviously correct, deliberately naive answer to ``as_of``."""
    latest: dict[date, Observation] = {}
    for item in observations:
        if item.series_id != series_id or item.knowledge_time > at:
            continue
        current = latest.get(item.event_time)
        if current is None or item.knowledge_time > current.knowledge_time:
            latest[item.event_time] = item
    return [latest[event] for event in sorted(latest)]


class StoreContract:
    """Behaviour every ``AsOfStore`` must have. Subclasses provide ``make_store``."""

    #: How many generated examples each property test tries. Slow stores can lower it.
    property_examples = 100

    def make_store(self) -> AsOfStore:
        raise NotImplementedError

    def loaded(self) -> AsOfStore:
        store = self.make_store()
        store.append(three_versions())
        return store

    def prop(self, **strategies: Any) -> Callable[[Callable[..., None]], Callable[[], None]]:
        """Turn a check function into a Hypothesis test using ``property_examples``."""

        def decorate(check: Callable[..., None]) -> Callable[[], None]:
            runner: Callable[[], None] = settings(
                deadline=None, max_examples=self.property_examples
            )(given(**strategies)(check))
            return runner

        return decorate

    # --- as_of ---------------------------------------------------------------------

    def test_empty_store_knows_nothing(self) -> None:
        store = self.make_store()
        assert store.as_of(SERIES, FIRST) == []
        assert store.revisions(SERIES, EVENT) == []

    def test_nothing_is_known_before_the_first_publication(self) -> None:
        assert self.loaded().as_of(SERIES, FIRST - timedelta(microseconds=1)) == []

    def test_the_boundary_is_inclusive(self) -> None:
        [known] = self.loaded().as_of(SERIES, FIRST)
        assert known.value == 4.00

    def test_as_of_returns_the_latest_version_known_at_that_time(self) -> None:
        store = self.loaded()
        one_second = timedelta(seconds=1)
        expected = [
            (SECOND - one_second, 4.00),
            (SECOND, 4.10),
            (THIRD - one_second, 4.10),
            (THIRD, 4.05),
            (datetime(2030, 1, 1, tzinfo=UTC), 4.05),
        ]
        for at, value in expected:
            [known] = store.as_of(SERIES, at)
            assert known.value == value, at

    def test_as_of_gives_one_version_per_event_time_in_order(self) -> None:
        store = self.make_store()
        store.append(
            [
                observation(event=date(2023, 1, 15), known=datetime(2023, 1, 30, tzinfo=UTC)),
                observation(event=date(2023, 1, 1), known=datetime(2023, 1, 14, tzinfo=UTC)),
                observation(event=date(2023, 1, 8), known=datetime(2023, 1, 20, tzinfo=UTC)),
                observation(event=date(2023, 1, 8), known=datetime(2023, 1, 27, tzinfo=UTC)),
            ]
        )
        late = [o.event_time for o in store.as_of(SERIES, datetime(2023, 2, 1, tzinfo=UTC))]
        assert late == [date(2023, 1, 1), date(2023, 1, 8), date(2023, 1, 15)]
        early = [o.event_time for o in store.as_of(SERIES, datetime(2023, 1, 25, tzinfo=UTC))]
        assert early == [date(2023, 1, 1), date(2023, 1, 8)]

    def test_series_are_isolated(self) -> None:
        store = self.loaded()
        store.append([observation(series="other", known=FIRST, value=9.0)])
        assert [o.value for o in store.as_of(SERIES, FIRST)] == [4.00]
        assert [o.value for o in store.as_of("other", FIRST)] == [9.0]
        assert store.as_of("unknown", THIRD) == []

    def test_as_of_rejects_a_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="must be timezone-aware"):
            self.loaded().as_of(SERIES, datetime(2023, 1, 14))

    def test_as_of_accepts_any_offset_for_the_same_instant(self) -> None:
        same_instant = datetime(2023, 1, 14, 7, 0, tzinfo=VIETNAM)  # == FIRST in UTC
        [known] = self.loaded().as_of(SERIES, same_instant)
        assert known.value == 4.00

    # --- revisions -----------------------------------------------------------------

    def test_revisions_are_the_full_history_oldest_first(self) -> None:
        store = self.make_store()
        store.append([observation(known=THIRD, value=3.0)])
        store.append([observation(known=FIRST, value=1.0), observation(known=SECOND, value=2.0)])
        history = store.revisions(SERIES, EVENT)
        assert [o.knowledge_time for o in history] == [FIRST, SECOND, THIRD]
        assert [o.value for o in history] == [1.0, 2.0, 3.0]

    def test_revisions_of_something_unknown_are_empty(self) -> None:
        store = self.loaded()
        assert store.revisions(SERIES, date(2030, 1, 1)) == []
        assert store.revisions("unknown", EVENT) == []

    # --- append --------------------------------------------------------------------

    def test_appending_nothing_is_fine(self) -> None:
        store = self.make_store()
        assert store.append([]) == 0
        assert store.as_of(SERIES, FIRST) == []

    def test_append_returns_the_number_of_new_versions(self) -> None:
        store = self.make_store()
        assert store.append(three_versions()) == 3
        assert store.append(three_versions()) == 0
        extra = observation(known=datetime(2023, 2, 4, tzinfo=UTC), value=4.2)
        assert store.append([*three_versions(), extra]) == 1

    def test_appending_an_identical_version_changes_nothing(self) -> None:
        store = self.loaded()
        again = observation(known=FIRST, value=4.00, ingest=datetime(2024, 1, 1, tzinfo=UTC))
        assert store.append([again]) == 0
        assert len(store.revisions(SERIES, EVENT)) == 3

    def test_the_same_key_with_another_value_conflicts(self) -> None:
        store = self.loaded()
        with pytest.raises(ConflictError):
            store.append([observation(known=FIRST, value=9.9)])
        assert store.as_of(SERIES, FIRST)[0].value == 4.00

    def test_the_same_key_with_another_source_conflicts(self) -> None:
        store = self.loaded()
        with pytest.raises(ConflictError):
            store.append([observation(known=FIRST, value=4.00, source="elsewhere")])

    def test_a_failed_append_stores_nothing_from_the_batch(self) -> None:
        store = self.loaded()
        fresh = observation(known=datetime(2023, 2, 4, tzinfo=UTC), value=4.2)
        with pytest.raises(ConflictError):
            store.append([fresh, observation(known=FIRST, value=9.9)])
        assert [o.knowledge_time for o in store.revisions(SERIES, EVENT)] == [FIRST, SECOND, THIRD]

    def test_a_conflict_inside_one_batch_stores_nothing(self) -> None:
        store = self.make_store()
        with pytest.raises(ConflictError):
            store.append([observation(value=1.0), observation(value=2.0)])
        assert store.revisions(SERIES, EVENT) == []

    def test_identical_duplicates_inside_one_batch_are_stored_once(self) -> None:
        store = self.make_store()
        assert store.append([observation(), observation()]) == 1
        assert len(store.revisions(SERIES, EVENT)) == 1

    def test_non_observations_are_rejected_and_nothing_is_stored(self) -> None:
        store = self.make_store()
        batch: list[Any] = [observation(), "not an observation"]
        with pytest.raises(TypeError):
            store.append(batch)
        assert store.revisions(SERIES, EVENT) == []

    def test_append_accepts_any_iterable(self) -> None:
        store = self.make_store()
        assert store.append(item for item in three_versions()) == 3

    def test_results_are_copies(self) -> None:
        store = self.loaded()
        store.as_of(SERIES, THIRD).clear()
        store.revisions(SERIES, EVENT).clear()
        assert len(store.as_of(SERIES, THIRD)) == 1
        assert len(store.revisions(SERIES, EVENT)) == 3

    # --- fidelity: what goes in comes back identical -------------------------------

    def test_ingest_time_and_microseconds_are_preserved(self) -> None:
        known = datetime(2023, 1, 14, 8, 30, 15, 123456, tzinfo=UTC)
        stored = observation(known=known, ingest=datetime(2024, 5, 1, 1, 2, 3, 654321, tzinfo=UTC))
        store = self.make_store()
        store.append([stored])
        assert store.revisions(SERIES, EVENT) == [stored]
        assert store.as_of(SERIES, known) == [stored]
        assert store.as_of(SERIES, known - timedelta(microseconds=1)) == []

    @pytest.mark.parametrize("value", [0.1, -2.5e-10, 1e308, 5e-324, 0.0, -0.0])
    def test_float_values_round_trip_exactly(self, value: float) -> None:
        store = self.make_store()
        store.append([observation(value=value)])
        [got] = store.revisions(SERIES, EVENT)
        assert got.value == value
        assert math.copysign(1.0, got.value) == math.copysign(1.0, value)

    def test_unicode_text_round_trips(self) -> None:
        stored = observation(series="Hà Nội/sốt xuất huyết", source="Bộ Y tế")
        store = self.make_store()
        store.append([stored])
        assert store.as_of("Hà Nội/sốt xuất huyết", FIRST) == [stored]

    # --- properties ----------------------------------------------------------------

    def test_as_of_matches_a_brute_force_oracle(self) -> None:
        @self.prop(specs=version_specs, hours=query_hours, series_id=st.sampled_from(SERIES_IDS))
        def check(specs: list[Spec], hours: int, series_id: str) -> None:
            observations = build(specs)
            store = self.make_store()
            store.append(observations)
            at = at_hours(hours)
            assert store.as_of(series_id, at) == oracle(observations, series_id, at)

        check()

    def test_as_of_never_returns_knowledge_from_the_future(self) -> None:
        @self.prop(specs=version_specs, hours=query_hours, series_id=st.sampled_from(SERIES_IDS))
        def check(specs: list[Spec], hours: int, series_id: str) -> None:
            store = self.make_store()
            store.append(build(specs))
            at = at_hours(hours)
            result = store.as_of(series_id, at)
            assert all(item.knowledge_time <= at for item in result)
            event_times = [item.event_time for item in result]
            assert event_times == sorted(set(event_times))

        check()

    def test_knowledge_only_grows_over_time(self) -> None:
        @self.prop(
            specs=version_specs,
            first=query_hours,
            second=query_hours,
            series_id=st.sampled_from(SERIES_IDS),
        )
        def check(specs: list[Spec], first: int, second: int, series_id: str) -> None:
            store = self.make_store()
            store.append(build(specs))
            early, late = sorted((first, second))
            before = {o.event_time: o for o in store.as_of(series_id, at_hours(early))}
            after = {o.event_time: o for o in store.as_of(series_id, at_hours(late))}
            assert before.keys() <= after.keys()
            assert all(after[key].knowledge_time >= before[key].knowledge_time for key in before)

        check()

    def test_insertion_order_does_not_matter(self) -> None:
        @self.prop(data=st.data(), specs=version_specs, hours=query_hours)
        def check(data: st.DataObject, specs: list[Spec], hours: int) -> None:
            observations = build(specs)
            shuffled = data.draw(st.permutations(observations))
            in_order, out_of_order = self.make_store(), self.make_store()
            in_order.append(observations)
            out_of_order.append(shuffled)
            at = at_hours(hours)
            for series_id in SERIES_IDS:
                assert in_order.as_of(series_id, at) == out_of_order.as_of(series_id, at)
                for offset in range(5):
                    event = BASE.date() + timedelta(days=offset)
                    assert in_order.revisions(series_id, event) == out_of_order.revisions(
                        series_id, event
                    )

        check()

    def test_splitting_or_repeating_appends_changes_nothing(self) -> None:
        @self.prop(
            specs=version_specs, split=st.integers(min_value=0, max_value=40), hours=query_hours
        )
        def check(specs: list[Spec], split: int, hours: int) -> None:
            observations = build(specs)
            cut = min(split, len(observations))
            whole, pieces = self.make_store(), self.make_store()
            assert whole.append(observations) == len(observations)
            pieces.append(observations[:cut])
            pieces.append(observations[cut:])
            assert pieces.append(observations) == 0
            at = at_hours(hours)
            for series_id in SERIES_IDS:
                assert whole.as_of(series_id, at) == pieces.as_of(series_id, at)

        check()
