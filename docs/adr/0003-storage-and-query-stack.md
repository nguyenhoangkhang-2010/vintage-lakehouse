# ADR 0003: Storage and query stack

- Status: Proposed (to be confirmed by `feature/iceberg-store`)

## Context

The store must keep every version of every observation, answer `as_of` queries quickly, and
run on a single development machine (Windows, Python 3.10+) without a cluster.

## Decision (proposed)

- Tables: Apache Iceberg through PyIceberg, with a local file catalog.
- Queries: DuckDB.
- Object storage: local filesystem by default. MinIO is optional and added only if an
  S3-style deployment is needed for a benchmark.
- A plain Parquet and DuckDB implementation is the fallback.

## To verify before accepting

- PyIceberg supports the write and read operations needed on this platform.
- DuckDB can read the resulting tables with acceptable latency.
- The store passes the same contract tests as the in-memory reference store.

## Alternatives considered

- Delta Lake: comparable features; Iceberg is preferred for its open specification and
  engine neutrality.
- Parquet only: simplest, and kept as the fallback if Iceberg adds friction without value.

## Consequences

Dependencies are added in `feature/iceberg-store`, not in the skeleton.
