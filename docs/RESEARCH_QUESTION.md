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
- **Latest reported period** `L(t)`: the most recent event_time that has any version known at
  `t`. Data is reported with a delay, so this is usually behind the calendar.
- **Horizon** `h`: the target is the period `h` steps after `L(t)`. Horizon 1 is the period
  right after the latest reported one. One step is one reporting period (7 days for weekly data).

## Evaluation protocol

For every forecast origin `t` in the evaluation window and every model (specified precisely in
[ADR 0007](adr/0007-replay-protocol.md)):

1. **Real-time run**: the model sees `as_of(t)`, one value per period. It produces quantile
   forecasts for the targets `L(t) + h` for each horizon `h`.
2. **Hindsight run**: same model, same origin, same targets, but the model sees the *same
   periods* (those up to `L(t)`) with their values as known at `T_eval`, that is `as_of(T_eval)`
   restricted to event_times up to `L(t)`. It never sees periods the real-time run could not
   see, only their settled values.
3. Score both forecasts against the same truth: the value of each target at `T_eval`. Using the
   same truth for both runs isolates the effect of the information available at fit time.
   Targets with no value at `T_eval` are not scored, in either run.

**Leakage gap** = `(WIS_realtime - WIS_hindsight) / WIS_realtime`, using mean WIS over matched
pairs (same model, origin and horizon in both runs). A positive gap means that evaluating on
revised data makes the model look better than it would have been in real time.

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
