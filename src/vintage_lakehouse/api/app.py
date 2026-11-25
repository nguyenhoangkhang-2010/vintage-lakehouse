"""Read-only HTTP API over ``as_of`` queries, and the server that exposes it.

The request logic is a plain function, ``Api.handle(method, target)``, so that it is tested
without sockets. ``make_server`` wraps it in the standard library's threading HTTP server. See
docs/adr/0012-read-only-api-and-demo.md for the endpoints, the safety rules and the limits: this
is a demo server (no TLS, no authentication), bound to the local machine by default.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from vintage_lakehouse.storage.base import AsOfStore
from vintage_lakehouse.storage.model import Observation, to_utc

#: A time later than any real publication, to read the latest version of every period.
FAR_FUTURE = datetime(2200, 1, 1, tzinfo=timezone.utc)
MAX_TARGET_LENGTH = 2048
_JSON = "application/json; charset=utf-8"
_HTML = "text/html; charset=utf-8"
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@dataclass(frozen=True)
class Response:
    """What to send back: a status, a body and its type, and extra headers."""

    status: int
    body: bytes
    content_type: str
    headers: dict[str, str] = field(default_factory=dict)


class _HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _json(status: int, payload: Any) -> Response:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return Response(status, body, _JSON, dict(_SECURITY_HEADERS))


def _load_page() -> str:
    return resources.files("vintage_lakehouse.api").joinpath("static/index.html").read_text("utf-8")


def _inline_hashes(page: str) -> str:
    """CSP source expressions allowing exactly the page's own inline script and style."""
    sources: list[str] = []
    for tag in ("script", "style"):
        for body in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", page, flags=re.DOTALL):
            digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
            sources.append(f"'sha256-{digest}'")
    return " ".join(sources)


def _parse_at(values: dict[str, list[str]]) -> datetime:
    text = _single(values, "at", required=True)
    return _parse_time(text, "at")


def _parse_time(text: str, name: str) -> datetime:
    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise _HttpError(400, f"{name} must be an ISO 8601 date or time, got {text!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return to_utc(parsed, name)


def _parse_date(text: str, name: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise _HttpError(400, f"{name} must be a date like 2023-06-30, got {text!r}") from None


def _single(values: dict[str, list[str]], name: str, *, required: bool) -> str:
    found = values.get(name, [])
    if len(found) > 1:
        raise _HttpError(400, f"{name} must be given once")
    if not found or not found[0].strip():
        if required:
            raise _HttpError(400, f"{name} is required")
        return ""
    return found[0]


def _observation(o: Observation) -> dict[str, Any]:
    return {
        "event_time": o.event_time.isoformat(),
        "value": o.value,
        "knowledge_time": o.knowledge_time.isoformat(),
    }


class Api:
    """The endpoints of ADR 0012 over one store and an allow-list of series."""

    def __init__(self, store: AsOfStore, series: Sequence[str]) -> None:
        self._store = store
        self._series = list(series)
        self._lock = threading.Lock()
        self._page = _load_page()
        self._csp = (
            "default-src 'self'; "
            f"script-src {_inline_hashes(self._page)}; "
            f"style-src {_inline_hashes(self._page)}; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )

    def handle(self, method: str, target: str) -> Response:
        """Answer one request. Never raises: every failure becomes a JSON error response."""
        try:
            return self._dispatch(method.upper(), target)
        except _HttpError as error:
            return _json(error.status, {"error": error.message})
        except Exception:  # a store failure must not leak paths or internals
            return _json(500, {"error": "internal error"})

    def _dispatch(self, method: str, target: str) -> Response:
        if len(target) > MAX_TARGET_LENGTH:
            raise _HttpError(414, "request target too long")
        if method not in ("GET", "HEAD"):
            response = _json(405, {"error": f"method {method} not allowed"})
            return Response(405, response.body, _JSON, {**response.headers, "Allow": "GET, HEAD"})
        response = self._route(target)
        if method == "HEAD":
            headers = {**response.headers, "Content-Length": str(len(response.body))}
            return Response(response.status, b"", response.content_type, headers)
        return response

    def _route(self, target: str) -> Response:
        split = urlsplit(target)
        query = parse_qs(split.query, keep_blank_values=True)
        parts = split.path.split("/")
        if split.path == "/":
            return Response(
                200,
                self._page.encode("utf-8"),
                _HTML,
                {**_SECURITY_HEADERS, "Content-Security-Policy": self._csp},
            )
        if split.path == "/api/health":
            return _json(200, {"status": "ok"})
        if split.path == "/api/series":
            return _json(200, {"series": self._series})
        if len(parts) == 5 and parts[:3] == ["", "api", "series"]:
            series_id = unquote(parts[3])
            if series_id not in self._series:
                raise _HttpError(404, f"unknown series {series_id!r}")
            endpoint = parts[4]
            if endpoint == "range":
                return self._range(series_id)
            if endpoint == "as_of":
                return self._as_of(series_id, query)
            if endpoint == "revisions":
                return self._revisions(series_id, query)
        raise _HttpError(404, "not found")

    def _range(self, series_id: str) -> Response:
        with self._lock:
            latest = self._store.as_of(series_id, FAR_FUTURE)
            first_versions = (
                self._store.revisions(series_id, latest[0].event_time) if latest else []
            )
        if not latest:
            return _json(200, {"series": series_id, "periods": 0})
        return _json(
            200,
            {
                "series": series_id,
                "first_event_time": latest[0].event_time.isoformat(),
                "last_event_time": latest[-1].event_time.isoformat(),
                "first_knowledge_time": first_versions[0].knowledge_time.isoformat(),
                "last_knowledge_time": max(o.knowledge_time for o in latest).isoformat(),
                "periods": len(latest),
            },
        )

    def _as_of(self, series_id: str, query: dict[str, list[str]]) -> Response:
        at = _parse_at(query)
        lower = _single(query, "from", required=False)
        first = _parse_date(lower, "from") if lower else None
        with self._lock:
            known = self._store.as_of(series_id, at)
        if first is not None:
            known = [o for o in known if o.event_time >= first]
        return _json(
            200,
            {
                "series": series_id,
                "at": at.isoformat(),
                "count": len(known),
                "observations": [_observation(o) for o in known],
            },
        )

    def _revisions(self, series_id: str, query: dict[str, list[str]]) -> Response:
        period = _parse_date(_single(query, "event_time", required=True), "event_time")
        with self._lock:
            versions = self._store.revisions(series_id, period)
        if not versions:
            raise _HttpError(404, f"no versions of {period.isoformat()} in {series_id!r}")
        return _json(
            200,
            {
                "series": series_id,
                "event_time": period.isoformat(),
                "versions": [
                    {"knowledge_time": v.knowledge_time.isoformat(), "value": v.value}
                    for v in versions
                ],
            },
        )


def make_server(api: Api, host: str, port: int) -> ThreadingHTTPServer:
    """A threading HTTP server answering with ``api`` (port 0 picks a free port)."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "vintage-lakehouse"
        sys_version = ""

        def _respond(self) -> None:
            response = api.handle(self.command, self.path)
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header(
                "Content-Length", response.headers.get("Content-Length", str(len(response.body)))
            )
            for name, value in response.headers.items():
                if name != "Content-Length":
                    self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.body)

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _respond

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
