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
- **No uncertainty on the leakage gap yet.** The harness gives point estimates. Confidence
  intervals and the sensitivity to the evaluation time `T_eval` come with the experiment.
- **Origins and the step are chosen by the caller.** The harness assumes evenly spaced periods
  (7 days by default) and does not detect gaps in a series.
