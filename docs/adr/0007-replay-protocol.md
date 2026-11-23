# ADR 0007: Replay protocol

- Status: Accepted (implemented in `feature/replay-harness`)

## Context

The headline result is how much hindsight evaluation overstates real-time forecast skill.
That number is only meaningful if the two runs differ in exactly one thing, the information
available when the model is fitted. The protocol has several places where a careless choice
leaks information or changes what is being compared, so each choice is written down.

## Decision

- **Origins.** The caller gives a set of forecast origins (timestamps). The harness does not
  choose them, because the right cadence depends on the source. Origins are sorted and
  de-duplicated.
- **What a forecaster sees.** One observation per period, in event_time order, passed as an
  immutable tuple. Real time: `as_of(origin)`. Hindsight: `as_of(T_eval)` restricted to
  event_times up to the latest period reported at the origin. Hindsight therefore reveals
  later *values* of known periods, never *periods* that were not yet reported.
- **Targets.** Horizon `h` is the period `h` steps after the latest reported period at the
  origin, with a configurable step (7 days by default). Horizons are counted from the latest
  *reported* period, not from the calendar date of the origin.
- **Truth.** The value of each target in `as_of(T_eval)`, identical for both runs. A target
  without a value at `T_eval` is not scored in either run. `T_eval` must not be before the last
  origin, otherwise the run would not be hindsight, and the harness refuses it.
- **Pairing.** Both runs are made for the same model, origin and horizon in the same loop, and
  the leakage gap only uses matched pairs.
- **Scores.** Weighted interval score (WIS, Bracher et al. 2021) on central intervals plus the
  median, absolute error of the median, and coverage of each central interval. Forecasts must
  have the median and symmetric, non-crossing quantiles; invalid forecasts are errors that name
  the model.
- **Leakage gap.** `(mean WIS real time - mean WIS hindsight) / mean WIS real time` per model
  and horizon, and pooled over horizons. It is undefined (reported as `None`) when the
  real-time WIS is zero.
- **Determinism.** Given a store, a configuration and deterministic forecasters, the result is
  identical on every run, whatever order the versions were loaded in, and on every store
  implementation. Forecasters must be deterministic; a forecaster that needs randomness must
  take its own seed.

## Safeguards (tested)

- A property-based test checks, for random series and origins, that the real-time history equals
  `as_of(origin)`, never contains a version known after the origin, and that the hindsight
  history has exactly the periods up to the latest reported one, with the `as_of(T_eval)` values.
- Hand-computed scores on a series with a known first-release bias check the whole chain:
  WIS, truth selection, pairing and the gap (80% for the test series).
- WIS is checked against an independent implementation (the average pinball loss).
- The Iceberg store gives byte-identical replay results to the in-memory store.

## Consequences

- Results depend on the choice of `T_eval`. The experiment branch reports a sensitivity
  analysis over it.
- Models that use the *knowledge times* of their history (for example a backfill model that
  looks at how old each value is) see different knowledge times in the two runs. That is
  intended: they are given the same periods with the information a hindsight evaluation would
  have had.
- Confidence intervals for the gap, and the baseline forecasters, are not part of this
  decision; they come with `feature/leakage-gap-experiment` and
  `feature/baseline-forecasters`.
