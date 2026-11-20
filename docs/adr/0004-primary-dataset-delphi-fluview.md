# ADR 0004: Primary dataset is Delphi FluView

- Status: Accepted (pending the licence check in `docs/DATA_SOURCES.md`)

## Context

The week-0 gate (see `docs/DATA_SOURCES.md`) needs a source that keeps every published version
of a value. Delphi FluView passed criteria 1, 2, 3 and 5 on a sample: each sampled week had
many versions, values changed between versions, the history spans more than six years, and
anonymous requests were not throttled. ALFRED could not be evaluated without an API key.

## Decision

Use Delphi FluView (national weighted ILI to start) as the primary dataset for the replay
harness and the headline experiment. Treat a second source as optional work, not a blocker.

## Consequences

- Headline results will be about US influenza surveillance data. The README and the technical
  report state this plainly, and Vietnam-specific claims are not made.
- A full pull probably needs a free API key. The key is read from an environment variable and
  is never committed.
- If the licence check reveals restrictions on redistribution, raw data stays out of the
  repository and only code, derived statistics and instructions are published.
- A second domain (for example ALFRED) can be added later without changing the store contract.
