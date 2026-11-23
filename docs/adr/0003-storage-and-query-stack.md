# ADR 0003: Storage and query stack

- Status: Accepted (implemented in `feature/iceberg-store`)

## Context

The store must keep every version of every observation, answer `as_of` queries quickly, and
run on a single development machine (Windows, Python 3.10+) without a cluster.

## Decision

- Tables: Apache Iceberg through PyIceberg, with a local SQLite catalog (`SqlCatalog`).
- Queries: PyIceberg prunes the scan (series and knowledge_time predicates are pushed down),
  then DuckDB picks the latest version per event_time with a window query.
- Object storage: local filesystem. MinIO stays optional and is only added if an S3-style
  deployment is needed for a benchmark.
- Packaging: an optional `iceberg` extra, which the `dev` extra includes. The core package
  (time model, contract, in-memory store) has no third-party dependencies.
- A plain Parquet and DuckDB implementation remains the fallback. It was not needed.

## What was verified

Environment: Windows 11, Python 3.10.11, pyiceberg 0.12.0, duckdb 1.5.6, pyarrow 25.0.1.

- PyIceberg creates, appends to, scans and reloads the table on this platform. Yes.
- The Iceberg store passes the same contract tests as the in-memory store, including the
  property tests against the brute-force oracle. Yes.
- DuckDB latency. Informal single-machine measurements (median of 5 runs, one series):

  | Rows in table | Bulk append | `as_of` | `revisions` | Append 100 rows |
  |---------------|-------------|---------|-------------|-----------------|
  | 10,000 | 0.10 s | 27 ms | 6 ms | 24 ms |
  | 100,000 | 0.27 s | 28 ms | 8 ms | 23 ms |

  These are indicative only: one run, one machine, one data layout. The real benchmarks are
  planned in `feature/benchmarks`.

## Findings that shaped the implementation

- **Windows paths.** The warehouse must be passed as a plain path. A `file:///C:/...` URI is
  misread by PyIceberg on Windows and fails with "filename syntax is incorrect".
- **Time zones.** Timestamps are decoded through integer microseconds since the epoch, not
  through Arrow's time zone objects, so no time zone database is needed.
- **File locks.** An open SQLite catalog file cannot be deleted on Windows. `IcebergStore.close()`
  releases it.
- **Typing.** PyIceberg's expression classes are pydantic models for which mypy synthesises a
  wrong constructor. They are typed as `Any` in one place, with a comment, and covered by tests.
- **The knowledge_time predicate is repeated in SQL.** The answer does not depend on the
  pushdown being exact.
- **Absolute paths.** Iceberg writes the locations of its files into the catalog. A warehouse
  given as a relative path (the command-line default is `data`) therefore recorded relative
  locations and could only be opened from the folder where it was created. This was found when
  a real store was opened from another folder. `IcebergStore.local` now makes the path absolute.
  A warehouse built before that fix fails to open elsewhere with a `WarehouseLocationError` that
  says how to rebuild it from the raw archive (no download is needed).
- **DuckDB 2 and Arrow.** Converting a DB-API result to Arrow warns in DuckDB 2.x. The query uses
  `connection.sql(..., params=...)` instead, and pytest turns that warning into an error.
- **No-op appends do not commit.** Duplicate-only and empty batches create no Iceberg snapshot,
  and one batch is one snapshot.

## Limits (known, not yet addressed)

- **Single writer.** The duplicate and conflict check reads the table before writing, so two
  concurrent writers could both pass it.
- **Many small appends create many small data files.** There is no compaction yet.
- **Unpartitioned table.** `as_of` loads all rows of a series up to the requested time into
  memory. Partitioning or sorting is a decision for the benchmark branch, with numbers.
- **Iceberg time travel is not used for `as_of`** (see ADR 0002); it remains available for
  auditing the store's own writes.

## Alternatives considered

- Delta Lake: comparable features; Iceberg is preferred for its open specification and
  engine neutrality.
- Parquet only: simplest, and kept as the fallback if Iceberg adds friction without value.
- String row filters instead of expression objects: avoids the typing issue but requires
  escaping series ids inside the filter text, which is riskier than a typed workaround.

## Consequences

- CI installs `.[dev]`, which includes the Iceberg extra, so the Iceberg tests run in CI. They
  are skipped automatically when the extra is not installed.
- Iceberg property tests use fewer generated examples (12 instead of 100) because creating a
  table on disk is slow compared with the in-memory store.
