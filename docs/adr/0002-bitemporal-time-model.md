# ADR 0002: Bitemporal time model

- Status: Accepted (implemented in `feature/time-model`)

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

## Validation rules

Enforced when an `Observation` is created:

- `series_id` and `source` are non-empty strings.
- `event_time` is a `date`, not a `datetime`.
- `knowledge_time` is timezone-aware and is converted to UTC. Its UTC date must not be before
  `event_time`: a version cannot be known before the period it describes begins.
- `value` is a finite real number. `bool`, NaN and infinities are rejected.
- `ingest_time` is optional; when present it is timezone-aware and converted to UTC.
- `ingest_time` is not required to be after `knowledge_time`, because date-only publication
  stamps (below) are converted conservatively and may fall after the true load time.

Not modelled yet: a version that retracts a value (a missing value). It will need its own
decision when a source requires it.

## Date-only publication stamps

Some sources (for example Delphi FluView) give only the day a version was released. A value
released on day `D` may have appeared at any moment of that day, so it is treated as known from
the start of `D + 1` (00:00 UTC) by `knowledge_time_from_date`. This can only delay when a value
counts as known, never advance it, so `as_of` queries cannot leak a value that was not yet
available. The cost is that a real version released early on day `D` is invisible to queries
made later that same day.

## Consequences

- Correctness depends on the quality of the source's issue dates; sources without them cannot
  be used (see the data gate).
- Iceberg time travel remains useful for audit and reproducibility of our own writes, but it
  is not the mechanism for `as_of`.
- A property-based test enforces `as_of(t)` never returns a version with
  `knowledge_time > t`.
