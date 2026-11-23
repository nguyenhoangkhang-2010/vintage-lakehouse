# data/

Local data lives here and is **git-ignored** (only this file and `.gitkeep` are tracked).

Layout written by `vintage-lakehouse ingest`:

```text
data/
  raw/<source>/<region>/   responses exactly as received, named by SHA-256, plus index.jsonl
  warehouse/               the Iceberg tables and their SQLite catalog
  lineage/ingestion.jsonl  one record per request: what was read and what it changed
```

The raw archive is the dataset of record (see ADR 0006). Back it up: if the source disappears
it may be the only copy.

Never commit raw data from a source whose licence does not allow redistribution. Record the
source, retrieval date and licence in `docs/DATA_SOURCES.md` instead.

The location can be changed with the `VINTAGE_LAKEHOUSE_DATA_DIR` environment variable.
