# ADR 0005: Store contract semantics

- Status: Accepted (implemented in `feature/reference-store`)

## Context

Several stores will answer `as_of` queries: a simple in-memory reference store now, and an
Iceberg-backed store later. They must behave identically, and the behaviour must be precise
enough to test, including the edge cases (boundaries, duplicates, conflicts).

## Decision

The contract is the `AsOfStore` protocol, tested by `tests/store_contract.py`. Any
implementation subclasses `StoreContract` and runs the same tests. The rules:

- **Inclusive boundary.** `as_of(t)` includes a version with `knowledge_time == t`. This is
  consistent with `knowledge_time_from_date`, which produces midnight stamps.
- **Latest known version per period.** For each `event_time` with a version known by `t`, return
  the version with the latest `knowledge_time <= t`, ordered by `event_time`.
- **Identity.** A version is identified by (series_id, event_time, knowledge_time).
- **Idempotent appends.** Re-appending a version with the same value and source is a no-op.
  `ingest_time` is informational and is ignored for this comparison.
- **Conflicts are errors.** The same key with a different value or source raises
  `ConflictError`. Silently overwriting would hide source corrections and data corruption.
- **Atomic batches.** If any item of a batch is invalid or conflicts, none of it is stored.
- **Plain results.** Results are new lists; unknown series or periods give empty lists; `as_of`
  needs a timezone-aware time and accepts any offset.

The in-memory store is the executable specification. The tests compare every store with a
deliberately naive brute-force oracle written in the test code, using property-based testing.

## Consequences

- The Iceberg store (`feature/iceberg-store`) must pass the same contract tests unchanged.
- A source that legitimately corrects a published version in place (same `knowledge_time`)
  cannot be stored under this contract. It would have to publish a new `knowledge_time`, or a
  new ADR would define how corrections are represented.
- The in-memory store is not designed for large data.
