# ADR 0002: Bitemporal time model

- Status: Proposed (becomes Accepted when `feature/time-model` is merged)

## Context

The core question depends on knowing exactly what was knowable at a past time. A value has two
different times: the period it describes and the moment it became available. Revised values
share the first and differ in the second.

Table formats such as Iceberg record a snapshot time (when *we* wrote the data). That is not
the same as when the *source* published it: history loaded today carries publication dates in
the past, and a snapshot timestamp cannot express them.

## Decision

Each observation carries three explicit times:

| Field | Meaning |
|-------|---------|
| `event_time` | The period the value describes |
| `knowledge_time` | When the source made this version available (timezone-aware, UTC) |
| `ingest_time` | When this project loaded the row (informational) |

`as_of(t)` uses only `knowledge_time`: for each `event_time` it returns the latest version with
`knowledge_time <= t`. `ingest_time` is never used to answer `as_of` queries.

## Consequences

- Correctness depends on the quality of the source's issue dates; sources without them cannot
  be used (see the data gate).
- Iceberg time travel remains useful for audit and reproducibility of our own writes, but it
  is not the mechanism for `as_of`.
- A property-based test enforces `as_of(t)` never returns a version with
  `knowledge_time > t`.
