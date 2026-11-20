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

_None yet._
