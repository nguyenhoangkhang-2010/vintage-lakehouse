# ADR 0013: Benchmarks

- Status: Accepted. Written before the numbers were collected.

## Context

The README promises three systems numbers: `as_of` query latency against table size, ingestion
throughput with revisions, and the storage overhead of keeping every version compared with
keeping a full snapshot at every release. Performance numbers are easy to get wrong (warm caches,
one lucky run, an unstated machine), so the method is fixed first.

## Decisions

### What is measured

1. **Query latency vs table size.** `as_of(series, t)` for the in-memory reference store and the
   Iceberg store, on synthetic data, in two sweeps:
   - *many series:* 1, 10 and 100 series of 400 weekly periods with 20 versions per period
     (about 8 thousand versions per series), querying one series. This shows whether a bigger
     table slows a query that only needs one series (the scan is pruned by series).
   - *long history:* one series of 400, 1,600 and 6,400 periods. This shows how one query scales
     with the amount of data it must read.
   Each point is the median and 95th percentile of 30 queries at random release times, after 3
   warm-up queries (seed 0). The latest-version query and `revisions` of one period are timed too.
2. **Ingestion throughput.** Versions per second for `append`, into an empty store and into a
   store that already holds a table, with batches of one series each (the shape of the
   ingestion pipeline's chunks).
3. **Storage overhead of versioning.** For the same data: the number of rows and the Parquet size
   of (a) all versions, as stored, and (b) a full snapshot of the series at every release (what
   one would keep without a bitemporal model), written with the same writer and compression.
   Also the size on disk of the Iceberg table. On the real FluView store (optional) the same
   comparison is made on the real versions.

### How it is measured

- `time.perf_counter`, one process, no other work started by the benchmark. Times include the
  Python conversion of results to `Observation`s, because that is what a caller pays.
- Synthetic data is deterministic (seeded). Values are random doubles, which compress badly, so
  storage ratios on synthetic data are pessimistic for both layouts; the real-data run shows how
  it behaves on real values.
- The Iceberg store is a local SQLite catalog on the local disk, the setup the project uses.
  Remote object storage would change every latency number.
- Every result is written with a **machine description** (OS, CPU, cores, memory when known,
  Python and library versions), the configuration and the seed. Numbers from one machine are
  not claimed for another.

### What the results will not claim

- Production performance, concurrency (the store is one-writer) or remote storage.
- Anything about stores other than the two measured.
- That synthetic timing predicts real timing beyond the real-data points that are measured.

## Safeguards (tested)

- The synthetic generator is deterministic and its version counts match the formula.
- The snapshot row count is hand-computed on a small case.
- Summaries (median, percentiles) are checked against known values.
- Every benchmark runs end to end on tiny sizes in the test suite, on both stores, and gives
  structurally complete results. Timings are never asserted.

## Addendum after the first run

The first full run showed that an Iceberg query for one series got slower as the table gained
other series (72 ms at 1 series, 434 ms at 100), which the plan did not anticipate. Diagnosing
it showed that planning a scan grows with the number of commits and data files. A fourth
measurement was therefore added, which loads the same table as one commit per series and as one
commit (about 3.6 times faster to query, 9 times faster to load). The other measurements and
their parameters were not changed. The diagnosis also found that Iceberg's default 16-character
string statistics defeat file pruning for long, similar series ids, which did not change latency
here. Details and the figures are in `docs/BENCHMARKS.md`.

## Consequences

- `vintage-lakehouse benchmark` is the new command, with a `--quick` preset for a few seconds and
  the full run for the published numbers (minutes).
- Results are not committed (they depend on the machine). The README quotes the figures with
  the machine and the command, and `docs/BENCHMARKS.md` explains how to reproduce them.
