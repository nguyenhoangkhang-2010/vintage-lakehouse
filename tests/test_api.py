"""Tests for the read-only HTTP API: the request logic, and a real server on a free port."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import quote

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backfill_fixture import SERIES, make_store, report_time, revised_series, varying_truth, week
from vintage_lakehouse.api import Api, make_server
from vintage_lakehouse.storage import InMemoryStore, Observation

N = 40


def build_store() -> InMemoryStore:
    return make_store(revised_series(N, varying_truth))


@pytest.fixture
def api() -> Api:
    return Api(build_store(), [SERIES])


def get(api: Api, target: str) -> tuple[int, Any]:
    response = api.handle("GET", target)
    assert response.content_type.startswith("application/json")
    return response.status, json.loads(response.body)


def as_of_url(at: str, series: str = SERIES, extra: str = "") -> str:
    return f"/api/series/{quote(series, safe='')}/as_of?at={quote(at)}{extra}"


# --- the simple endpoints ------------------------------------------------------------------


def test_health_and_series_list(api: Api) -> None:
    assert get(api, "/api/health") == (200, {"status": "ok"})
    assert get(api, "/api/series") == (200, {"series": [SERIES]})


def test_range_gives_the_bounds_for_a_slider(api: Api) -> None:
    status, body = get(api, f"/api/series/{SERIES}/range")
    assert status == 200
    assert body == {
        "series": SERIES,
        "first_event_time": week(0).isoformat(),
        "last_event_time": week(N - 1).isoformat(),
        "first_knowledge_time": report_time(0).isoformat(),
        "last_knowledge_time": report_time(N - 1).isoformat(),
        "periods": N,
    }


def test_series_ids_with_slashes_are_url_encoded() -> None:
    store = InMemoryStore()
    store.append(revised_series(5, [1.0] * 5, series="a/b"))
    api = Api(store, ["a/b"])
    assert get(api, "/api/series/a%2Fb/range")[0] == 200
    assert get(api, "/api/series/a/b/range")[0] == 404


# --- as_of ---------------------------------------------------------------------------------


def test_as_of_returns_what_was_known_then(api: Api) -> None:
    at = report_time(10)
    status, body = get(api, as_of_url(at.isoformat()))
    assert status == 200 and body["count"] == 11 and body["series"] == SERIES
    expected = {o.event_time.isoformat(): o for o in build_store().as_of(SERIES, at)}
    assert [o["event_time"] for o in body["observations"]] == sorted(expected)
    for item in body["observations"]:
        original = expected[item["event_time"]]
        assert item["value"] == pytest.approx(original.value)
        assert item["knowledge_time"] == original.knowledge_time.isoformat()
    # week 10 is a first release (80%), week 9 a second report (90%), the rest are final
    values = {o["event_time"]: o["value"] for o in body["observations"]}
    assert values[week(10).isoformat()] == pytest.approx(0.8 * varying_truth(10))
    assert values[week(9).isoformat()] == pytest.approx(0.9 * varying_truth(9))
    assert values[week(8).isoformat()] == pytest.approx(varying_truth(8))


def test_the_boundary_is_inclusive(api: Api) -> None:
    exact = report_time(10)
    _, at_boundary = get(api, as_of_url(exact.isoformat()))
    _, just_before = get(api, as_of_url((exact - timedelta(seconds=1)).isoformat()))
    assert at_boundary["count"] == 11 and just_before["count"] == 10


def test_before_anything_was_published_the_answer_is_empty(api: Api) -> None:
    status, body = get(api, as_of_url("2000-01-01"))
    assert status == 200 and body["count"] == 0 and body["observations"] == []


def test_from_limits_the_periods_returned(api: Api) -> None:
    _, body = get(api, as_of_url(report_time(10).isoformat(), extra=f"&from={week(5)}"))
    assert body["observations"][0]["event_time"] == week(5).isoformat()
    assert body["count"] == 6


def test_time_zones_are_respected_and_a_missing_one_means_utc(api: Api) -> None:
    moment = report_time(10)
    plain = get(api, as_of_url(moment.replace(tzinfo=None).isoformat()))[1]
    explicit = get(api, as_of_url(moment.isoformat()))[1]
    zulu = get(api, as_of_url(moment.replace(tzinfo=None).isoformat() + "Z"))[1]
    shifted = get(api, as_of_url(moment.astimezone().isoformat()))[1]
    assert plain == explicit == zulu == shifted
    offset = get(api, as_of_url("2023-03-12T12:00:00+07:00"))[1]
    same = get(api, as_of_url("2023-03-12T05:00:00+00:00"))[1]
    assert offset == same


@settings(max_examples=60, deadline=None)
@given(seconds=st.integers(min_value=-100_000, max_value=60 * 24 * 3600))
def test_nothing_in_an_answer_is_newer_than_the_question(seconds: int) -> None:
    api = Api(build_store(), [SERIES])
    at = report_time(0) + timedelta(seconds=seconds)
    status, body = get(api, as_of_url(at.isoformat()))
    assert status == 200
    assert all(o["knowledge_time"] <= at.isoformat() for o in body["observations"])
    assert body["count"] == len(build_store().as_of(SERIES, at))


# --- revisions -----------------------------------------------------------------------------


def test_revisions_lists_every_version_oldest_first(api: Api) -> None:
    status, body = get(api, f"/api/series/{SERIES}/revisions?event_time={week(3)}")
    assert status == 200
    assert [v["knowledge_time"] for v in body["versions"]] == [
        report_time(3).isoformat(),
        report_time(4).isoformat(),
        report_time(5).isoformat(),
    ]
    assert [v["value"] for v in body["versions"]] == pytest.approx(
        [0.8 * varying_truth(3), 0.9 * varying_truth(3), varying_truth(3)]
    )
    assert body["event_time"] == week(3).isoformat()


def test_an_unknown_period_is_404(api: Api) -> None:
    status, body = get(api, f"/api/series/{SERIES}/revisions?event_time=1999-01-03")
    assert status == 404 and "error" in body


# --- bad requests ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "status"),
    [
        (f"/api/series/{SERIES}/as_of", 400),
        (f"/api/series/{SERIES}/as_of?at=", 400),
        (f"/api/series/{SERIES}/as_of?at=yesterday", 400),
        (f"/api/series/{SERIES}/as_of?at=2023-13-45", 400),
        (f"/api/series/{SERIES}/as_of?at=2023-06-01&at=2023-07-01", 400),
        (f"/api/series/{SERIES}/as_of?at=2023-06-01&from=soon", 400),
        (f"/api/series/{SERIES}/as_of?at=2023-06-01&from=2023-06-01T10:00:00", 400),
        (f"/api/series/{SERIES}/revisions", 400),
        (f"/api/series/{SERIES}/revisions?event_time=nope", 400),
        (f"/api/series/{SERIES}/revisions?event_time=2023-06-01T00:00:00", 400),
        ("/api/series/not-served/range", 404),
        ("/api/series/not-served/as_of?at=2023-06-01", 404),
        (f"/api/series/{SERIES}/nothing", 404),
        ("/api/nothing", 404),
        ("/elsewhere", 404),
        ("/" + "a" * 3000, 414),
    ],
)
def test_bad_requests_get_a_status_and_a_json_error(api: Api, target: str, status: int) -> None:
    got, body = get(api, target)
    assert got == status
    assert isinstance(body["error"], str) and body["error"]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_nothing_but_get_is_allowed(api: Api, method: str) -> None:
    response = api.handle(method, "/api/health")
    assert response.status == 405
    assert response.headers["Allow"] == "GET, HEAD"


def test_the_store_is_never_modified(api: Api) -> None:
    store = build_store()
    before = {o.key for o in store.as_of(SERIES, report_time(N))}
    served = Api(store, [SERIES])
    for target in (as_of_url("2024-01-01"), f"/api/series/{SERIES}/range"):
        served.handle("GET", target)
    served.handle("POST", as_of_url("2024-01-01"))
    assert {o.key for o in store.as_of(SERIES, report_time(N))} == before
    assert isinstance(next(iter(store.as_of(SERIES, report_time(N)))), Observation)


def test_internal_errors_do_not_leak_details() -> None:
    class Broken(InMemoryStore):
        def as_of(self, series_id: str, at: Any) -> Any:
            raise RuntimeError("secret path C:\\data\\warehouse")

    response = Api(Broken(), [SERIES]).handle("GET", as_of_url("2024-01-01"))
    body = json.loads(response.body)
    assert response.status == 500
    assert "secret" not in response.body.decode() and body["error"] == "internal error"


# --- the page -------------------------------------------------------------------------------


def test_the_demo_page_is_html_and_loads_nothing_external(api: Api) -> None:
    response = api.handle("GET", "/")
    page = response.body.decode("utf-8")
    assert response.status == 200 and response.content_type.startswith("text/html")
    assert "<title>" in page and 'type="range"' in page
    # nothing is loaded from elsewhere: no absolute or protocol-relative src/href, no CSS
    # url() or @import, and every request goes to a relative /api path
    assert not re.search(r"""(src|href|action)\s*=\s*["']?(https?:)?//""", page)
    assert "url(" not in page and "@import" not in page
    calls = re.findall(r'getJson\(\s*"([^"]+)"', page)
    assert calls and all(url.startswith("/api/") for url in calls)
    assert " style=" not in page  # inline style attributes would need a weaker CSP
    assert response.headers["Cache-Control"] == "no-store"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_head_returns_headers_without_a_body(api: Api) -> None:
    response = api.handle("HEAD", "/api/health")
    assert response.status == 200 and response.body == b""
    assert response.headers["Content-Length"] == str(len(api.handle("GET", "/api/health").body))


# --- a real server -----------------------------------------------------------------------


@pytest.fixture
def server_url() -> Iterator[str]:
    server = make_server(Api(build_store(), [SERIES]), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host!s}:{port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()


def fetch(url: str, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_a_real_request_over_http(server_url: str) -> None:
    status, headers, body = fetch(f"{server_url}/api/health")
    assert status == 200 and json.loads(body) == {"status": "ok"}
    assert headers["Content-Type"].startswith("application/json")
    status, _, body = fetch(f"{server_url}{as_of_url(report_time(10).isoformat())}")
    assert status == 200 and json.loads(body)["count"] == 11


def test_a_real_server_refuses_writes_and_unknown_paths(server_url: str) -> None:
    assert fetch(f"{server_url}/api/health", "POST")[0] == 405
    assert fetch(f"{server_url}/api/health", "DELETE")[0] == 405
    assert fetch(f"{server_url}/nothing")[0] == 404
    assert fetch(f"{server_url}/")[1]["Content-Type"].startswith("text/html")


def test_many_concurrent_requests_all_get_correct_answers(server_url: str) -> None:
    url = f"{server_url}{as_of_url(report_time(20).isoformat())}"
    results: list[int] = []

    def worker() -> None:
        _, _, body = fetch(url)
        results.append(json.loads(body)["count"])

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert results == [21] * 16
