# data/

Local data lives here and is **git-ignored** (only this file and `.gitkeep` are tracked).

Suggested layout once ingestion exists:

```text
data/
  raw/         responses exactly as received from a source
  interim/     normalised intermediate files
  processed/   tables and results derived by the pipeline
```

Never commit raw data from a source whose licence does not allow redistribution. Record the
source, retrieval date and licence in `docs/DATA_SOURCES.md` instead.

The location can be changed with the `VINTAGE_LAKEHOUSE_DATA_DIR` environment variable.
