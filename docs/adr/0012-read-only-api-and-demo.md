# ADR 0012: Read-only HTTP API and the time-slider demo

- Status: Accepted.

## Context

The project's central idea is that the same series looks different depending on *when* you look.
A reader should be able to see that without reading code: pick a date, see the series as it was
known then, next to what it turned into. The roadmap asks for a read-only API over `as_of`
queries and a demo that runs from documented commands.

## Decisions

### Standard library only

The server is built on `http.server.ThreadingHTTPServer`, with no new dependency. The API has four
read-only endpoints and one page, the load is one person moving a slider, and a dependency
(FastAPI, uvicorn) would add install weight, a larger supply-chain surface and a moving API for
no functional gain. The cost is stated: this is a demo server, not a production one (no TLS, no
authentication, no rate limiting, no HTTP/2, basic concurrency). Request handling is a plain
function, `Api.handle(method, target) -> Response`, so the logic is tested without sockets and
could be mounted in another framework unchanged.

### Endpoints (all `GET`, JSON)

| Path | Result |
|------|--------|
| `/api/health` | `{"status": "ok"}` |
| `/api/series` | The series the server was started with |
| `/api/series/<id>/range` | First and last event_time, and first and last knowledge_time, for slider bounds |
| `/api/series/<id>/as_of?at=<time>[&from=<date>]` | What was known at `at`: one version per period, with its knowledge_time (optionally only periods from `from`) |
| `/api/series/<id>/revisions?event_time=<date>` | Every version of one period, oldest first |
| `/` | The demo page |

`<id>` is URL-encoded (series ids contain slashes). `at` is an ISO 8601 date or time; a value
without a time zone is read as UTC. Responses carry the `knowledge_time` of every value, so a
client can see that nothing in an `as_of` answer is newer than `at`.

### Safety

- **Read-only:** only `GET`; anything else is `405`. The store is never written.
- **Allow-list of series:** only the series named at start-up are served; any other id is `404`.
  Query values are parsed strictly; bad input is `400` with a JSON message, never a stack trace.
- **Local by default:** it binds `127.0.0.1`. Binding elsewhere is an explicit `--host`, and the
  command prints a warning that there is no authentication.
- **No external resources:** the page is one file with inline script and style, so it works
  offline and loads nothing from third parties. No cookies, no CORS headers.
- **One store, one lock:** store access is serialised with a lock, because the stores are not
  promised to be thread-safe.

### The demo

A single page. A slider moves the "as of" moment week by week between the first and last
publication. The chart draws the series as known at that moment in a strong line, and the
series as known now in a thin one, so revisions show as the gap between them at the right-hand
end, and a table lists the latest weeks with their value then, their value now and the revision.
Keyboard control works (the slider is a native input), and the readout is announced to screen
readers.

## Safeguards (tested)

- An `as_of` answer never contains a value with `knowledge_time` after `at`, including at the
  exact boundary (checked against the store for many moments).
- Every bad input (missing, malformed or naive-but-invalid values, unknown series, unknown path,
  other HTTP methods) gets the right status and a JSON error.
- A real server on an ephemeral port answers over HTTP, and shuts down cleanly.
- The page is served with the right content type and loads nothing external.

## Consequences

- `vintage-lakehouse serve` is the new command. It reads the Iceberg store (extra `iceberg`).
- Each `as_of` call costs about 70 ms on the national series (see LIMITATIONS), which is fine for
  a slider but not for many users.
- The API serves whole series, not aggregates. Trust reports and leak checks stay on the command
  line.
