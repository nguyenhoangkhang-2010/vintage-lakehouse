# Roadmap and branch plan

One roadmap item is one branch. Branches start from `develop` and merge back through a pull
request (see [GIT_WORKFLOW.md](GIT_WORKFLOW.md)). Durations are estimates, to be adjusted.

| Order | Branch | Deliverable | Done when |
|-------|--------|-------------|-----------|
| 1 | `chore/project-skeleton` | Layout, tooling, CI, docs | CI is green on the PR |
| 2 | `feature/week0-data-gate` | Script that probes candidate sources and records the findings in `docs/DATA_SOURCES.md` | At least one source passes the gate, or the plan is revised |
| 3 | `feature/time-model` | `Observation` model with validated event/knowledge times | Unit tests cover valid and invalid timestamps |
| 4 | `feature/reference-store` | In-memory reference store and the store contract tests | Property test: `as_of(t)` never returns knowledge_time > `t` |
| 5 | `feature/iceberg-store` | Iceberg-backed store, DuckDB queries | Passes the same contract tests as the reference store |
| 6 | `feature/revision-ingestion` | Source client, revision detection, lineage | Re-ingesting the same vintage is idempotent |
| 7 | `feature/replay-harness` | Real-time and hindsight replay with scoring | Replay of one series is deterministic |
| 8 | `feature/baseline-forecasters` | Seasonal naive, ETS/ARIMA, gradient boosting | Each baseline has a documented sanity test |
| 9 | `feature/leakage-gap-experiment` | Headline experiment with confidence intervals and `T_eval` sensitivity | One command reproduces the results table |
| 10 | `feature/backfill-model` | Revision model and corrected forecasts | Gap recovery reported with uncertainty |
| 11 | `feature/trust-report-and-linter` | Dataset trust report and look-ahead linter | Linter flags a seeded leak in a test pipeline |
| 12 | `feature/api-and-dashboard` | Read-only API and a time-slider demo | Demo runs from documented commands |
| 13 | `feature/benchmarks` | Latency, throughput and storage-overhead benchmarks | Numbers and the commands that produce them are in the README |
| 14 | `docs/technical-report` | 4 to 8 page technical report | Limitations section is complete |

**Status:** all 14 branches are complete as of version 0.1.0. The schedule below was an
estimate made at the start.

## Estimated schedule (adjust to reality)

| Week | Focus |
|------|-------|
| 0 | Branches 1 to 2: skeleton and the data gate |
| 1 to 2 | Branches 3 to 6: time model, stores, ingestion |
| 3 | Branches 7 to 8: replay harness and baselines |
| 4 | Branches 9 to 10: headline experiment and backfill model |
| 5 | Branches 11 to 13: linter, demo, benchmarks |
| 6 | Branch 14: report, release `v0.1.0` |

## Gate rule

If the week-0 gate fails (no source with enough revision history), do not start branch 3.
Revise the data plan first and record the decision as an ADR.
