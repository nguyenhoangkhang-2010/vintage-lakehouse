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
