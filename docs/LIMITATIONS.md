# Limitations

This file is part of the project's definition of done. Add to it whenever a branch reveals a
new limitation, and keep entries factual.

## Known from the design

- **Dataset dependence.** Results hold for the datasets evaluated. The leakage gap is not a
  universal constant and depends on how a source revises its data.
- **Data availability.** Public sources that preserve revision history are limited, and
  sources for Vietnam with full vintages may not exist. Any Vietnam-specific claim must be
  backed by a source that passes the gate in [DATA_SOURCES.md](DATA_SOURCES.md).
- **Choice of `T_eval`.** The point at which data is treated as "final" affects the numbers.
  The sensitivity to this choice is reported, not hidden.
- **Simple baselines.** Models are intentionally simple. Conclusions about the gap for
  complex or large models are not claimed.
- **Not an operational system.** The project is research and decision-support tooling, not a
  source of official forecasts or warnings.

## Discovered during development

- **First dataset is US influenza surveillance.** The week-0 gate passed on Delphi FluView
  (national weighted ILI). Findings are about that data and cannot be generalised to other
  diseases, countries or domains without testing them. Not Vietnamese data.
- **Only early revisions were sampled.** The gate looked at the first 20 weekly issues of each
  sampled week. Later revisions are not yet characterised.
- **Anonymous access worked for a full national pull** (17 requests, no throttling), but it is
  documented as rate-limited. Pulls for many regions were not tested and may need a free key.
- **Licence not yet verified** for redistributing derived results (see `DATA_SOURCES.md`).
- **ALFRED / FRED not evaluated.** It needs an API key registered by the project owner.
- **The legacy Delphi API is being retired** (ADR 0006). Only the national region was pulled,
  and whether the V5 API offers the same history is unchecked. Reproducibility rests on the
  raw archive, which is not committed to the repository.
- **Same-day releases.** Date-only stamps give two issues released on the same day the same
  knowledge_time. Identical values are stored once (this happened once in the national series).
  Different values would stop ingestion with a conflict, and a rule for that case is not yet
  decided.
- **Ingestion reads one period at a time from the store.** Revision detection calls
  `revisions()` for each event_time in a chunk. That is fine for a year of weekly data (about 50
  calls), but slow for chunks with thousands of periods; a bulk read would need a contract
  change.
- **Many small commits.** Each chunk is one Iceberg commit, so a 17-year pull creates 17 small
  data files; there is no compaction yet.
- **Replay cost.** The harness calls `as_of` once per origin. On the Iceberg store with the
  national series that is about 70 ms per origin (548 weekly origins took about 37 seconds).
  Many series or many more origins will need a bulk read.
- **The experiment's intervals may still be somewhat too narrow.** They come from a
  moving-block bootstrap with 12-week blocks, but a flu season is about 30 weeks long, so
  dependence beyond a block is not preserved (ADR 0009). They cover the choice of origins, not
  model misspecification or the choice of `T_eval`; the sensitivity table covers the latter.
- **One series, three simple models, origins every 4 weeks.** The experiment says nothing about
  other series, diseases or countries, nor about forecasting methods in general. The 4-week
  cadence was chosen to keep gradient boosting affordable.
- **The `T_eval` grid is late.** All four evaluation times are at least 13 weeks after the last
  origin, and the gap did not change between them because influenza data settles quickly. The
  experiment therefore cannot say what a hindsight time that is early relative to each origin
  would do. That needs an evaluation time defined relative to each origin.
- **The excluded pandemic window is an analyst's choice** (2020-03-01 to 2021-09-30). It is
  stated and reported next to the unfiltered result, not tuned.
- **The gap can be negative.** A first release biased low can accidentally pull a naive forecast
  towards a falling series. The relative gap is unstable when real-time WIS is small, so the
  absolute degradation is always reported too.
- **Exploratory analyses have no multiple-comparison correction** (model differences, revision
  size groups). They are reported with intervals and described, not tested.
- **Origins and the step are chosen by the caller.** The harness assumes evenly spaced periods
  (7 days by default) and does not detect gaps in a series. The baselines forward-fill a
  missing period.
- **The baselines are simple and untuned.** Conclusions apply to these models, not to
  forecasting methods in general (ADR 0008). ETS has no seasonal component, and its normal
  prediction intervals are an approximation.
- **Gradient boosting is slow.** About 4.4 s per origin on the national FluView series (both
  modes), against 0.2 s for the other baselines, so a full replay over every weekly origin
  would take about 40 minutes and needs thinning (one run on one machine).
- **Baselines need a minimum history** (60 to 100 periods). Shorter origins must be skipped via
  `ReplayConfig.min_history`.
- **Older warehouses must be rebuilt.** A warehouse created before the absolute-path fix
  records relative file locations and only opens from the folder where it was created. It can be
  rebuilt from the raw archive without a download (`data/README.md`). New warehouses are not
  affected.
- **The backfill correction is deliberately simple** (ADR 0010). It is the median revision of
  the last four reported weeks by age, fixed in advance and not tuned. It cannot recover any
  part of the gap that comes from older values, from revisions that depend on the level or the
  season, or from a change in how the source revises (a bias that shifts over time is estimated
  from the past and applied to the present).
- **The API and demo are a demo server.** Standard library only, no TLS, no authentication, no
  rate limiting, and every request queries the store (about 0.2 s for the national series). It
  is read-only and local by default, but it is not meant to be exposed to a network.
- **The trust report's thresholds are conventions** (2% bias, 5 points of drift, 4 periods to
  settle, 1% tolerance), not tested limits, and the report describes revisions that already
  happened. It does not predict how a source will revise in future.
- **The leak check only finds what its origins exercise**, assumes the pipeline is deterministic
  and reads data only through the store it is given. It does not look inside models. An origin
  with no later data is inconclusive.
- **The static lint is a heuristic** with both false positives and false negatives (a leak
  through a variable or a helper in another module is not seen).
- **The correction does not widen the forecast intervals.** It changes the values the model
  sees, so the uncertainty of the correction itself is not in the prediction intervals.
- **The correction needs the vintages of past periods**, not only the latest value of each, so
  it cannot be applied to data that was stored without its revision history.
- **Recovery is only defined where there is a gap.** The share of the gap recovered is computed
  for models whose own gap interval excludes zero; for the others only the improvement is given.
  A ratio of two noisy quantities, it can exceed 100% or be negative.
- **The revision pattern of FluView is not stationary**, so the correction fixed in advance
  (an estimate over all settled past weeks) was wrong in the early evaluation years and did not
  recover the gap (`docs/RESULTS.md`). Whether a recent-window estimate would is untested, and
  was suggested by the results, so it needs its own plan or fresh data.
