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

## Rebuilding the warehouse from the raw archive

The warehouse can always be rebuilt from `raw/` without any download. This is needed if a
command says that the files of the warehouse could not be found. That happens for a warehouse
created by an older version with a relative path, which can only be opened from the folder where
it was created. Delete only `warehouse/` (keep `raw/` and `lineage/`) and run, from the project
folder:

```text
vintage-lakehouse ingest delphi-fluview --first-year 2010 --last-issue 202638 --from-archive
```

Use the same `--first-year`, `--last-issue` and `--region` as the original download. The result
has the same content as the original (checked on the national FluView data: same number of
versions and the same content hash).
