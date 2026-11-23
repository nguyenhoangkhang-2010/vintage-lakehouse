# ADR 0006: Archive raw responses; the legacy Delphi API is being phased out

- Status: Accepted (implemented in `feature/revision-ingestion`)

## Context

ADR 0004 chose Delphi FluView as the primary dataset. While building ingestion on 2026-11-23
two facts surfaced:

- The legacy `fluview` endpoint answers queries that lie entirely after 2026-09-22 with result
  code -3 and the message "this API no longer provides data with time values after 2026-09-22;
  it has been retired in favor of cast-api / epidata-etl".
- The documentation of Delphi's client packages describes the legacy endpoints moving to the V5
  API, with a tentative deprecation date of October 2026. This was found by web search and has
  not been checked against Delphi's own pages.

The historical data was still being served on 2026-11-23, and a full pull worked. What is not
known: when, or whether, the legacy endpoint will be removed; whether V5 offers the same
per-issue history; and under which terms.

If the endpoint disappears, the only copy of the revision history is whatever has been saved.

## Decision

- Every raw response is archived byte for byte, named by its SHA-256, with an append-only
  `index.jsonl` recording the request, the hash, the retrieval time and the size
  (`vintage_lakehouse.ingestion.archive`). Saving the same response twice is a no-op; a source
  that changes its answer produces a second archived response.
- Reading from the archive verifies the hash, so tampering or corruption is detected.
- Ingestion can be replayed entirely from the archive (`ingest ... --from-archive`) with no
  network access. Replay is deterministic and idempotent.
- Every lineage record carries `raw_sha256`, so each stored version can be traced to the exact
  bytes and the request that produced it.
- The archive is the dataset of record. It lives under the git-ignored data directory and is
  **not committed**, because the licence has not been checked. If redistribution turns out to be
  allowed, it can be shared; otherwise only the list of hashes is published, so others can check
  that they hold identical data.
- Sources are pluggable: a source yields chunks (a request, the raw bytes, the parsed
  observations). A V5 source can be added without touching the pipeline or the stores.
- Run a full pull soon and keep the archive.

## Evidence

Informal, one run on 2026-11-23 (anonymous access, national region, epiweeks 2010 to 2026,
publications up to issue 202638):

| Measure | Value |
|---------|-------|
| Requests | 17 (one per year) |
| Wall time, live pull | about 66 s |
| Rows fetched | 51,677 |
| Versions whose value changed from the previous version | 15,694 |
| Largest single revision (weighted ILI) | 0.543 |
| Raw archive size | about 14 MB |
| Replay from the archive, no network, into the same store | about 21 s, 0 new versions |

## Consequences

- Results are reproducible from the archive plus a fixed `--last-issue`, even if the API goes
  away.
- A response made before the cut-off is only as complete as what the legacy API returned.
  If V5 turns out to carry the same history, a V5 source and an ADR comparing the two are the
  next step.
- Storage grows with the number of regions and series (about 14 MB for one region).
- The archive format records the legacy response shape. A V5 source archives its own responses
  under its own source id.
