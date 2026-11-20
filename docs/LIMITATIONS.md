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
- **Full pull needs a free API key (likely).** Anonymous access is rate-limited according to the
  Delphi documentation. This was not tested for a full historical pull.
- **Licence not yet verified** for redistributing derived results (see `DATA_SOURCES.md`).
- **ALFRED / FRED not evaluated.** It needs an API key registered by the project owner.
