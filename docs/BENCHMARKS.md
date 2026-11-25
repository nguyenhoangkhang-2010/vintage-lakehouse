# Benchmarks

Method and limits: [ADR 0013](adr/0013-benchmarks.md). These are the numbers from one run on one
machine; the command that produced them is below, and the report it writes has every table.

**Machine:** Windows 11, Intel (20 logical cores), 39.7 GiB memory, local disk. Python 3.10.11,
PyIceberg 0.12.0, DuckDB 1.5.6, PyArrow 25.0.1. Iceberg with a local SQLite catalog.
**Data:** synthetic weekly series with 20 versions per period (random doubles), seed 0, and the
real FluView national series (873 periods, 51,676 versions).

```text
vintage-lakehouse benchmark --from-store --out results/benchmarks
```

About three minutes. `--quick` finishes in seconds with tiny sizes, and `--stores memory` skips
Iceberg. The run in this document was started on an otherwise idle machine, and a second run
gave the same figures within a few percent.

## Query latency

`as_of(series, t)` at random release times, 30 queries per point after 3 warm-up queries,
including the conversion of the result to observations.

| Table | In-memory store | Iceberg store |
|-------|-----------------|---------------|
| 1 series, 400 periods (7,810 versions) | 0.11 ms | 72 ms (p95 87) |
| 10 series (78,100 versions) | 0.12 ms | 114 ms |
| 100 series (781,000 versions) | 0.31 ms | 434 ms (p95 511) |
| 1 series, 1,600 periods (31,810 versions) | 1.6 ms | 86 ms |
| 1 series, 6,400 periods (127,810 versions) | 7.3 ms | 158 ms (p95 217) |
| Real FluView series (51,676 versions) | | 53 ms (p95 66) |

- **The in-memory store is a reference, not a competitor:** it scales with the data one query
  returns, and it holds everything in RAM.
- **For Iceberg, a query that needs one series gets slower as the table gets more series**
  (72 ms at 1 series, 434 ms at 100), although it reads the same 5,830 rows. The cause is the
  number of commits: each `append` is a commit with its own data file, and planning a scan has to
  read the metadata of all of them. Loading the *same* 781,000 versions as one commit gives
  107 ms instead of 389 ms for the latest-release query, and loading takes 4.5 s instead of 40 s
  (table "Fragmentation" in the report). One-off checks outside the repository agreed: planning a
  scan took 2 ms with 1 file, 34 ms with 10 and 336 ms with 100, against 6 ms for 100 series in
  one file.
- **File pruning was not the bottleneck.** Iceberg keeps only the first 16 characters of string
  statistics by default, so series ids that share a 16-character prefix (such as the synthetic
  `bench/series-0000` and `bench/series-0009`) are not told apart, and 10 files were read
  instead of 1. Fixing that (ids that differ early, or full column statistics) read 1 file
  but the latency did not change. Ids of this project's form `delphi/fluview/<region>/<measure>`
  have only one character of the region inside the first 16, so regions that start with the same
  letter, or two measures of one region, would not be told apart either.
- **One series with a long history** grows roughly with the amount read: 72 ms at 7.8 thousand
  versions, 158 ms at 128 thousand.

## Ingestion

| What | Throughput |
|------|-----------|
| `append`, one series per batch, Iceberg, empty table | 45,000 versions/s |
| `append`, same, into a table that already holds as many series | 40,000 versions/s |
| `append`, in-memory store | 177,000 to 252,000 versions/s |
| The whole pipeline from the raw FluView archive (read, detect revisions, append in 17 commits) | 51,676 versions in 34 s, about 1,500 versions/s |

The pipeline is roughly 25 to 30 times slower than a bare `append`. This is consistent with revision
detection reading the store one period at a time (`revisions` takes about 22 ms on this series,
and a 873-period series needs hundreds of calls), which `LIMITATIONS.md` already lists, but the
split was not profiled. The pipeline figure is a single run, timed outside the benchmark command.

## Storage: all versions against a snapshot at every release

| Table | Versions | Snapshot rows | Parquet, versions | Parquet, snapshots | Iceberg on disk |
|-------|----------|---------------|-------------------|--------------------|-----------------|
| Real FluView series | 51,676 | 377,086 (7.3x) | 0.19 MB | 0.57 MB (3.1x) | 0.48 MB |
| Synthetic, 400 periods | 7,810 | 80,200 (10x) | 0.08 MB | 0.13 MB (1.5x) | 0.11 MB |
| Synthetic, 1,600 periods | 31,810 | 1,280,800 (40x) | 0.34 MB | 3.05 MB (8.9x) | 0.35 MB |

- **Rows:** keeping a snapshot at every release grows with the square of the history (every
  release repeats the whole series), while the versions grow linearly. On the real series that is
  7.3 times as many rows.
- **Bytes:** Parquet compresses the repeated snapshot values, so the byte ratio is smaller than
  the row ratio (3.1x on real data). It still grows with history: 1.1x at 200 periods and 8.9x at
  1,600 on the synthetic data.
- **The versioned Iceberg table on disk (0.48 MB) is 2.5 times the plain Parquet of the
  versions (0.19 MB)** for the real series: it also stores the source and ingest-time columns,
  17 small files and the table metadata. This is a fixed cost that matters for small tables.
- The synthetic values are random, so they compress badly; the real-data row is the one to trust.

## What this does not show

- One machine and one run per number, local disk, one writer. Remote object storage, concurrent
  writers and other machines were not measured.
- The pipeline throughput is a single timing outside the benchmark command.
- The synthetic tables are not real data, apart from the real-data rows. A slow query on the
  Iceberg store (tens to hundreds of milliseconds) is fine for a slider or a replay of a few
  hundred origins, but not for many users.
- The explanation of the latency growth (commit count) is supported by the fragmentation
  benchmark and by one-off diagnostics, not by a profile of PyIceberg's internals.

## What to do about it (not done in this branch)

- **Compact the table** after bulk loads, or write whole backfills as one commit. The ingestion
  pipeline writes one commit per year, which is cheap for 17 years but not for thousands of
  series.
- **Bulk-read revisions** in ingestion, to remove the per-period `revisions` calls.
- **Full column statistics (or partitioning by series)** so that pruning works for long ids.
