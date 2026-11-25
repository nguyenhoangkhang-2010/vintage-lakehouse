# vintage-lakehouse

> A point-in-time (bitemporal) lakehouse for data that gets revised, plus a "time machine"
> that measures how much forecast accuracy is inflated by evaluating in hindsight.

**Status: early development.** The data layer, the replay harness, three baseline forecasters
and the first experiment exist. The results table below is filled in by the branches listed in
[docs/ROADMAP.md](docs/ROADMAP.md); a row says "not measured yet" until it is measured.

## Why this exists

Many public series (case counts, hydrology, economic statistics) are revised after first
publication. A forecaster who needs a prediction *today* only has today's provisional numbers.
Evaluating a model on the final, revised numbers lets it "see" information that did not exist
at forecast time, so the model looks better than it would have been in real time.

People who choose or fund forecasting models (public-health analysts, hydrology and policy
teams, researchers) need an honest estimate of real-time skill. This project builds the data
layer and the evaluation harness to produce it.

## The question

> By how much does hindsight evaluation overstate real-time forecast skill, and how much of
> that gap can be recovered by modelling data revisions?

Full protocol, definitions and metrics: [docs/RESEARCH_QUESTION.md](docs/RESEARCH_QUESTION.md).

## Components (each feeds the next)

| # | Component | Role |
|---|-----------|------|
| 1 | Bitemporal storage and `as_of` queries | Know exactly what was knowable at time *t* |
| 2 | Revision-aware ingestion | Record every revision with lineage |
| 3 | Replay harness | Re-run history in real time and compare with hindsight |
| 4 | Backfill model | Predict how provisional values will be revised |
| 5 | Trust report and leakage linter | Show a dataset's revision behaviour and flag look-ahead in user pipelines |

Architecture and data flow: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Results

US national weighted ILI (Delphi FluView), 137 origins from 2015 to 2025, horizons 1 to 4 weeks.
Full tables, caveats and how to reproduce: [docs/RESULTS.md](docs/RESULTS.md).

| Metric | Value | Dataset | Branch |
|--------|-------|---------|--------|
| Leakage gap, seasonal naive (relative WIS, 95% CI) | 0.2% [-0.2%, 0.6%] | FluView national wILI | `feature/leakage-gap-experiment` |
| Leakage gap, ETS | 6.4% [1.3%, 10.0%] | FluView national wILI | `feature/leakage-gap-experiment` |
| Leakage gap, gradient boosting | 2.6% [0.7%, 4.9%] | FluView national wILI | `feature/leakage-gap-experiment` |
| Gap recovered by a backfill correction (ETS, 95% CI) | no recovery: made ETS worse (-9.3% [-15.4%, -1.8%]); GBM +0.4% [-1.2%, 1.8%] | FluView national wILI | `feature/backfill-model` |
| `as_of` query latency vs table size | not measured yet | – | `feature/benchmarks` |
| Versioning storage overhead vs full snapshots | not measured yet | – | `feature/benchmarks` |

## Quickstart (development)

Requires Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pre-commit install
```

```powershell
pytest
ruff check .
ruff format --check .
mypy
vintage-lakehouse info
```

## Loading data

```text
vintage-lakehouse ingest delphi-fluview --first-year 2010 --last-issue 202638
```

This pulls every published version of the national weighted ILI from Delphi FluView, one request
per year, into a local Iceberg warehouse under `data/`. It saves each raw response and writes one
lineage record per request. Running it again is safe: versions that are already stored are
skipped. For each request it prints the new periods, the revisions (value changed from the
previous version) and the largest revision. `--from-archive` replays the saved responses
without any network access.

The legacy Delphi API is being phased out ([ADR 0006](docs/adr/0006-archive-raw-responses.md)),
so run a full pull soon and keep `data/raw`.

## Using a store

```python
from datetime import date, datetime, timezone

from vintage_lakehouse.storage import InMemoryStore, Observation

store = InMemoryStore()  # or IcebergStore.local("warehouse") from vintage_lakehouse.storage.iceberg
store.append(
    [
        Observation(
            "fluview", date(2023, 1, 1), datetime(2023, 1, 14, tzinfo=timezone.utc), 4.00, "demo"
        ),
        Observation(
            "fluview", date(2023, 1, 1), datetime(2023, 1, 21, tzinfo=timezone.utc), 4.10, "demo"
        ),
    ]
)
store.as_of("fluview", datetime(2023, 1, 15, tzinfo=timezone.utc))  # -> the 4.00 version
```

The Iceberg store needs the `iceberg` extra (`pip install -e ".[iceberg]"`, already part of
`.[dev]`). Both stores pass the same contract tests, see
[ADR 0005](docs/adr/0005-store-contract-semantics.md).

## Repository layout

```text
src/vintage_lakehouse/
  storage/     bitemporal store and as_of queries
  ingestion/   data sources and revision detection
  replay/      real-time replay and hindsight evaluation
  forecast/    baseline forecasters
  experiment/  the leakage-gap and backfill-recovery experiments (runners, statistics, reports)
  backfill/    vintage table, revision correction and the corrected forecaster
  audit/       trust report, leak check and lint (look-ahead in pipelines)
  api/         read-only HTTP API
tests/         unit, property and integration tests
docs/          research question, architecture, roadmap, ADRs, git workflow
data/          local data (git-ignored)
```

## Project documents

- [Research question](docs/RESEARCH_QUESTION.md)
- [Architecture](docs/ARCHITECTURE.md) and [ADRs](docs/adr)
- [Roadmap and branch plan](docs/ROADMAP.md)
- [Git workflow](docs/GIT_WORKFLOW.md) and [Contributing](CONTRIBUTING.md)
- [Data sources and week-0 gate](docs/DATA_SOURCES.md)
- [Baseline forecasters](docs/BASELINES.md)
- [Results](docs/RESULTS.md)
- [Running the experiments](docs/EXPERIMENT.md), with the designs of the [leakage gap](docs/adr/0009-leakage-gap-experiment.md) and of the [backfill correction](docs/adr/0010-backfill-correction.md)
- [Auditing a dataset and a pipeline](docs/AUDIT.md), designed in [ADR 0011](docs/adr/0011-trust-report-and-leakage-linter.md)
- [Limitations](docs/LIMITATIONS.md)

## Scope

This is research and decision-support tooling. It does not produce official forecasts or
warnings, and its findings depend on the datasets it is run on.
