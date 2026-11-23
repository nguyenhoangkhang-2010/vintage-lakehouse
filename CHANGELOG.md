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

### Changed
- ADR 0002 is accepted and now records the validation rules and the date-only convention.
- ADR 0003 is accepted with the verification results, findings and known limits.
- `dev` extra now includes the `iceberg` extra.
