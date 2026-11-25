# The time-slider demo and the read-only API

Designed in [ADR 0012](adr/0012-read-only-api-and-demo.md). It shows, without any code, that the
same series looks different depending on when you look.

## Run it

You need the data ingested (see the README, "Loading data") and the project installed with the
`iceberg` extra. From the project folder:

```text
vintage-lakehouse serve
```

Then open <http://localhost:8000/>. Options: `--port 8080`, `--data-dir path`, and `--series ID`
(repeatable; the default is the FluView national series). It binds to this machine only; `--host`
can change that, with a warning, because the server has no authentication or encryption.

## What you see

Move the slider (or use the arrow keys and the week buttons) to a date. The **thick line** is the
series as it was known then: for every week, the latest version published by that date. The **thin
line** is the series as it is known now. The circles are the latest weeks, still provisional at
that date. The table lists those weeks with their value then, their value now, the revision and
the date that version was published.

For example, as of 2022-07-30 the latest FluView week known was 2022-07-17 at 1.58 weighted ILI;
it is now 1.66, a revision of +4.8%.

## The API

All endpoints are `GET` and return JSON. Series ids contain slashes, so they are URL-encoded.

| Path | Result |
|------|--------|
| `/api/health` | `{"status": "ok"}` |
| `/api/series` | The series served |
| `/api/series/<id>/range` | First and last event and knowledge times, number of periods |
| `/api/series/<id>/as_of?at=<time>[&from=<date>]` | One version per period, as known at `at` |
| `/api/series/<id>/revisions?event_time=<date>` | Every version of one period, oldest first |

```text
curl "http://localhost:8000/api/series/delphi%2Ffluview%2Fnat%2Fwili/as_of?at=2020-03-07&from=2020-01-01"
```

`at` is an ISO 8601 date or time; without a time zone it is read as UTC. Nothing in an `as_of`
answer has a `knowledge_time` after `at`.

## Limits

A demo server, built on the standard library: no TLS, no authentication, no rate limiting, and
every request queries the store (about 0.2 s for the national series on the machine used).
It serves whole series, not aggregates. Trust reports and leak checks are command-line tools
([AUDIT.md](AUDIT.md)).
