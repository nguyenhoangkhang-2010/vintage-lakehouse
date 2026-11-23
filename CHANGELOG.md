# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Project skeleton: package layout, tooling (ruff, mypy, pytest), pre-commit, CI, and
  project documents (research question, architecture, roadmap, ADRs, git workflow).
- Week-0 data gate: offline-testable gate criteria, a Delphi FluView probe, MMWR epiweek
  helpers, and the `vintage-lakehouse gate delphi-fluview` command.
- Gate findings in `docs/DATA_SOURCES.md` and ADR 0004 (primary dataset).
- `network` pytest marker; network tests are deselected by default (`pytest -m network`).
- `Observation` bitemporal model with validated, UTC-normalised times, the `ObservationKey`
  version identity, and `knowledge_time_from_date` for date-only publication stamps.

- `AsOfStore` contract (`append`, `as_of`, `revisions`) with `ConflictError`, and the
  `InMemoryStore` reference implementation.
- Reusable store contract tests (`tests/store_contract.py`) including property-based tests
  against a brute-force oracle. ADR 0005 records the contract semantics.

- `IcebergStore`: Iceberg table (PyIceberg, local SQLite catalog) queried with DuckDB, passing
  the same contract tests as the in-memory store. New optional `iceberg` extra.
- Contract tests now also check round-tripping of microsecond timestamps, `ingest_time`,
  exact floats and Unicode text, and the property tests can be tuned per store.

- Ingestion pipeline: revision detection (new period, revised, unchanged), idempotent
  re-ingestion, atomic chunks, a SHA-256 raw-response archive with offline replay, and a
  JSON-lines lineage log.
- `FluViewSource` (Delphi FluView, one request per year) and the `vintage-lakehouse ingest`
  command with `--from-archive`.
- MMWR helpers `weeks_in_year` and `date_to_epiweek`.
- ADR 0006: archive raw responses because the legacy Delphi API is being phased out.

- Replay harness: `run_replay` forecasts every origin in real time (`as_of(origin)`) and in
  hindsight (`as_of(T_eval)` for the same periods), scores both against the same truth,
  and `leakage_gap` / `summarize` aggregate the result. Scoring: weighted interval score,
  absolute error and interval coverage. ADR 0007 records the protocol.

- Baseline forecasters: `SeasonalNaive` (no extra dependencies), `ETSForecaster` and
  `GradientBoostingForecaster` (optional `baselines` extra), sharing quantile levels, gap
  handling, non-crossing quantiles and an optional lower bound. Documented sanity tests for each
  (`docs/BASELINES.md`) and ADR 0008.

- The leakage-gap experiment: `vintage-lakehouse experiment leakage-gap` replays the baselines
  at several evaluation times (with real-time forecasts cached), computes the gap per model and
  horizon with moving-block bootstrap confidence intervals, compares models, analyses
  sensitivity to `T_eval` and to excluded origins, and relates the gap to the size of recent
  revisions. Writes a report, CSV files and a manifest (configuration, data identifier,
  library versions). ADR 0009 fixes the analysis plan; `docs/EXPERIMENT.md` explains running it.
- First measured results (`docs/RESULTS.md`): on US national weighted ILI, the leakage gap is
  0.2% for seasonal naive, 6.4% for ETS and 2.6% for gradient boosting, with block-bootstrap
  intervals.

### Fixed
- A warehouse created with a relative path (the default `--data-dir data`) could only be opened
  from the folder where it was created, because Iceberg stored relative file locations. The
  warehouse path is now made absolute, in `IcebergStore.local` and in the `ingest` command. A
  warehouse built by an older version gives a clear `WarehouseLocationError` with rebuild
  instructions, and `data/README.md` explains how to rebuild it from the raw archive.
- The as-of query no longer triggers DuckDB 2's "Arrow output on a DB-API result" warning.

### Changed
- ADR 0002 is accepted and now records the validation rules and the date-only convention.
- ADR 0003 is accepted with the verification results, findings and known limits.
- `dev` extra now includes the `iceberg` extra.
