# Research question

## Primary question

> By how much does hindsight evaluation overstate real-time forecast skill, and how much of
> that gap can be recovered by modelling data revisions?

## Definitions

- **event_time**: the reference period a value describes (for example an epidemiological week).
- **knowledge_time** (also "vintage" or "issue time"): when the source first made that value
  available. A revised value has a later knowledge_time than the value it replaces.
- **as_of(t)**: for each event_time, the latest value whose knowledge_time is at or before `t`.
  This is exactly what a forecaster could have seen at time `t`.
- **Hindsight data**: the values as known at a fixed, later evaluation time `T_eval`
  (after most revisions have settled).
- **Forecast origin**: a time `t` at which a forecast is issued.

## Evaluation protocol

For every forecast origin `t` in the evaluation window and every model:

1. **Real-time run**: fit or compute features using only `as_of(t)`. Produce a forecast.
2. **Hindsight run**: same model, same origin, but fit or compute features using
   `as_of(T_eval)` truncated to event_times up to `t`. Produce a forecast.
3. Score both forecasts against the same truth, taken from `as_of(T_eval)`. Using the same
   truth for both runs isolates the effect of the information available at fit time.

**Leakage gap** = relative difference in score between the hindsight run and the real-time run
(for example the percentage by which weighted interval score improves in hindsight).

## Hypotheses (to test, not to assume)

- H1: the gap is positive and grows with the size and delay of revisions in the series.
- H2: the gap differs between model classes.
- H3: a backfill-aware correction recovers a measurable fraction of the gap.

A gap near zero, or a correction that does not help, is a valid result and will be reported.

## Metrics

- Forecast skill: weighted interval score (WIS) and, where samples exist, CRPS; MAE for point
  forecasts; empirical coverage of the 80% and 95% intervals.
- Revision behaviour: mean absolute revision, relative revision, time until a value stabilises.
- Systems: `as_of` query latency vs table size, ingestion throughput with revisions,
  storage overhead of versioning vs full snapshots.
- Correctness: property-based test that `as_of(t)` never returns a value with
  knowledge_time later than `t`.

## Baseline models

Seasonal naive, an ETS or ARIMA model, and gradient boosting on lagged values. Models are kept
simple on purpose: the contribution is the evaluation protocol and the data layer.

## Threats to validity (tracked in docs/LIMITATIONS.md)

- Results depend on the datasets that expose full revision history.
- Which revisions are "typical" differs by source, so the gap is not a universal constant.
- The choice of `T_eval` affects the numbers and is reported as a sensitivity analysis.
