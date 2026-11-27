# ADR 0011: Dataset trust report and look-ahead checks

- Status: Accepted.

## Context

Two practical questions follow from the experiments (ADR 0009 and 0010):

1. *How much should I trust the latest values of this dataset?* The correction experiment failed
   because the revision bias of FluView changed over time, which a summary of revision behaviour
   by year would have shown beforehand.
2. *Does my pipeline use information that was not available when it claims to forecast?* A
   pipeline evaluated on a snapshot of today's data leaks revisions and future periods without
   any error.

## Decisions

### Trust report (`audit.trust`)

A description of the revision behaviour of one series, computed from its vintages (every stored
version) as of a moment (default: the latest knowledge_time). It reports facts, not a score:

- **Coverage:** periods, versions, first and last period, gaps in the regular series, periods
  still too recent to be settled.
- **Revision by age** (lag 0 is the latest reported period): for each lag, over periods that are
  settled (at least `settle` = 26 periods before the latest), the share of values that differ
  from the settled value, the share that differ by more than `tolerance` (1%), the median ratio
  settled / provisional, and the mean and 90th percentile of the absolute relative revision.
- **Settling:** the smallest lag from which a period stays within `tolerance` of its settled
  value, as a cumulative share by lag, with the median and 90th percentile; and the days from
  first release to the last revision.
- **Bias by year:** the median lag-0 ratio and mean absolute relative revision by calendar year
  of the period, with the number of periods.

**Findings** are short sentences produced by fixed rules, with the thresholds below. They are
prompts to look, not verdicts:

| Finding | Rule |
|---------|------|
| First release is biased | median lag-0 ratio differs from 1 by more than 2% |
| The bias drifts | among years with at least 20 settled periods, the yearly medians of the lag-0 ratio span more than 5 percentage points |
| Slow to settle | 90th percentile of the settling lag is above 4 periods |
| Gaps | any missing period in the regular series |
| Recent values are provisional | always stated: the number of periods not yet settled |

### Look-ahead checks (`audit.leak_check`, `audit.lint`)

**Dynamic check (the reliable one).** A pipeline is a function `pipeline(store, series_id,
origin)` that returns a result (a number, a mapping or a sequence of numbers). The check runs it
at each origin on the real data, and again on a copy in which every version published after the
origin is **altered** (values scaled), and once more with those versions **removed**. A pipeline
that only uses what was known at the origin cannot tell the difference. Any change in its result
is a look-ahead leak, reported per origin with the largest difference. An origin where the
alteration changes nothing (no later data exists) is reported as inconclusive, never as clean.
This finds revisions, future periods and snapshot reads, whatever the code looks like. It cannot
find a leak that the chosen origins do not exercise, and it assumes the pipeline is deterministic.

**Static check (a heuristic aid).** An AST scan of Python source for patterns that commonly
introduce look-ahead in time-series code: a negative `shift`, back-fill, centred rolling
windows, the wall clock, `as_of` called with a fixed or wall-clock time, `revisions()` (which
returns versions published after any origin unless filtered), and `train_test_split` that
shuffles. It has false positives (a negative shift to build a *target* is fine) and false
negatives (a leak through a helper in another module is not seen). It is a prompt for review,
and it is documented as such.

### What this does not do

- It does not decide whether a dataset is "good". It describes revisions.
- The dynamic check does not look inside a model or into data the pipeline reads from outside
  the store.
- Nothing here corrects or repairs a pipeline.

## Safeguards (tested)

- Trust-report figures are hand-computed on a series with known revisions
  (`tests/backfill_fixture.py`: factors 0.8, 0.9, 1.0), including the settling lag, the share of
  revised values and the yearly bias, and a series whose bias changes is flagged as drifting.
- A seeded leak is found by the dynamic check: a pipeline that reads the latest values with
  `as_of` at a far-future time, one that uses the last of `revisions()`, and one that uses a
  later period. Clean pipelines pass, including one that reads the whole history up to the
  origin.
- Every static rule has a positive and a negative example.

## Addendum after building it

- Defaults: lags 0 to 7 are tabulated (`max_lag` 7). The settling lag is searched over the whole
  settling window; a value that is not within tolerance even at the end of the window is
  recorded as `settle + 1` and printed as "more than 26 periods".
- On the real FluView series the report flagged the drift of the first-release bias (yearly
  median ratio from 0.975 to 1.127) and slow settling, with no forecast involved.
- Run on the project's own source, the lint reported nine findings, all benign, and it misses a
  leak whose time goes through a variable (shown by `examples/audit/leaky_pipeline.py`, found by
  the leak check).

## Consequences

- The `audit` package depends on `backfill.VintageTable` for vintages.
- The command line gets an `audit` group: `trust-report`, `lint` and `leak-check`.
- The pipeline for `leak-check` is loaded by `module:function` from the Python path, which runs
  user code. It is meant for the user's own pipelines.
