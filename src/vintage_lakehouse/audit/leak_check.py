"""Dynamic look-ahead check: does a pipeline's result depend on data published after its origin?

A pipeline is a function ``pipeline(store, series_id, origin)`` returning a result (a number, a
mapping or a sequence of numbers, or anything comparable with ``==``). For each origin it is run
three times on private copies of the data:

1. on the real data (twice, to check that it is deterministic);
2. with every version published after the origin **altered** (values scaled and shifted);
3. with every version published after the origin **removed**.

A pipeline that only uses what was known at the origin cannot tell these apart. If its result
changes, or it fails when later data is missing, it reads data it should not have: later
revisions, later periods, or a snapshot taken after the origin. If the alteration changes nothing
because no later data exists, the origin is inconclusive and never counted as clean. See
docs/adr/0011-trust-report-and-leakage-linter.md for what this can and cannot find.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from vintage_lakehouse.storage.base import AsOfStore
from vintage_lakehouse.storage.memory import InMemoryStore
from vintage_lakehouse.storage.model import Observation, to_utc

#: A time later than any real publication, for reading every version of a series.
FAR_FUTURE = datetime(2200, 1, 1, tzinfo=timezone.utc)

Pipeline = Callable[[AsOfStore, str, datetime], Any]

_RELATIVE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class OriginCheck:
    """The outcome at one origin.

    ``status`` is ``clean``, ``leak``, ``inconclusive`` (no later data to alter), ``error`` (the
    pipeline failed on the real data) or ``nondeterministic`` (two runs on the real data differ).
    """

    origin: datetime
    status: str
    max_difference: float
    detail: str


@dataclass(frozen=True)
class LeakCheckResult:
    """Outcomes for every origin."""

    series_id: str
    origins: list[OriginCheck]

    @property
    def leaks(self) -> list[OriginCheck]:
        """The origins at which the pipeline used later data."""
        return [c for c in self.origins if c.status == "leak"]

    @property
    def ok(self) -> bool:
        """True when at least one origin proved clean and none leaked or could not be checked."""
        bad = {"leak", "error", "nondeterministic"}
        return any(c.status == "clean" for c in self.origins) and not any(
            c.status in bad for c in self.origins
        )

    def summary(self) -> str:
        """One line per origin and a verdict."""
        lines = []
        for c in self.origins:
            label = c.status.upper() if c.status == "leak" else c.status
            lines.append(f"{c.origin.date().isoformat()}  {label}  {c.detail}".rstrip())
        if self.leaks:
            verdict = f"LEAK at {len(self.leaks)} of {len(self.origins)} origins."
        elif self.ok:
            verdict = f"No look-ahead found at {len(self.origins)} origins (clean)."
        else:
            verdict = "Not proven clean: see the origins above."
        return "\n".join([*lines, verdict])


def _flatten(result: Any) -> list[Any]:
    if isinstance(result, bool):
        return [result]
    if isinstance(result, int | float):
        return [float(result)]
    if isinstance(result, Mapping):
        return [item for key in sorted(result, key=repr) for item in _flatten(result[key])]
    if isinstance(result, Sequence) and not isinstance(result, str | bytes):
        return [item for element in result for item in _flatten(element)]
    return [result]


def _difference(a: Any, b: Any) -> float:
    """The largest numeric difference between two results (inf if they differ otherwise)."""
    left, right = _flatten(a), _flatten(b)
    if len(left) != len(right):
        return math.inf
    largest = 0.0
    for x, y in zip(left, right, strict=True):
        numeric = isinstance(x, float) and isinstance(y, float) and not isinstance(x, bool)
        if numeric:
            gap = abs(x - y)
            if gap > _RELATIVE_TOLERANCE * max(1.0, abs(x), abs(y)):
                largest = max(largest, gap)
        elif x != y:
            largest = math.inf
    return largest


def load_versions(store: AsOfStore, series_id: str, up_to: datetime) -> list[Observation]:
    """Every version of a series published at ``up_to`` or earlier."""
    limit = to_utc(up_to, "up_to")
    versions: list[Observation] = []
    for latest in store.as_of(series_id, limit):
        versions += [
            v for v in store.revisions(series_id, latest.event_time) if v.knowledge_time <= limit
        ]
    return versions


def _alter(version: Observation) -> Observation:
    return replace(version, value=version.value * 1.5 + 1.0)


def _store(versions: Iterable[Observation]) -> InMemoryStore:
    copy = InMemoryStore()
    copy.append(versions)
    return copy


def _run(
    pipeline: Pipeline, store: AsOfStore, series_id: str, origin: datetime
) -> tuple[bool, Any]:
    try:
        return True, pipeline(store, series_id, origin)
    except Exception as exc:  # the pipeline is user code
        return False, f"{type(exc).__name__}: {exc}"


def check_pipeline(
    store: AsOfStore,
    series_id: str,
    pipeline: Pipeline,
    origins: Sequence[datetime],
) -> LeakCheckResult:
    """Check a pipeline for look-ahead at each origin (see the module docstring).

    The pipeline is given private copies of the data, so ``store`` is never modified.
    """
    checked_origins = [to_utc(origin, "origin") for origin in origins]
    versions = load_versions(store, series_id, FAR_FUTURE)
    outcomes: list[OriginCheck] = []
    for origin in checked_origins:
        later = [v for v in versions if v.knowledge_time > origin]
        if not later:
            outcomes.append(
                OriginCheck(origin, "inconclusive", 0.0, "no data was published after this origin")
            )
            continue

        ok, base = _run(pipeline, _store(versions), series_id, origin)
        if not ok:
            outcomes.append(OriginCheck(origin, "error", 0.0, f"failed on the real data: {base}"))
            continue
        ok_again, again = _run(pipeline, _store(versions), series_id, origin)
        if not ok_again or _difference(base, again) > 0.0:
            outcomes.append(
                OriginCheck(origin, "nondeterministic", 0.0, "two runs on the same data differ")
            )
            continue

        earlier = [v for v in versions if v.knowledge_time <= origin]
        altered = [*earlier, *[_alter(v) for v in later]]
        details: list[str] = []
        largest = 0.0
        for label, data in (("altering", altered), ("removing", earlier)):
            worked, other = _run(pipeline, _store(data), series_id, origin)
            if not worked:
                details.append(f"{label} later data made it fail ({other})")
                largest = math.inf
                continue
            difference = _difference(base, other)
            if difference > 0.0:
                details.append(f"{label} later data changed the result by {difference:.6g}")
                largest = max(largest, difference)
        if details:
            outcomes.append(OriginCheck(origin, "leak", largest, "; ".join(details)))
        else:
            outcomes.append(OriginCheck(origin, "clean", 0.0, ""))
    return LeakCheckResult(series_id, outcomes)
