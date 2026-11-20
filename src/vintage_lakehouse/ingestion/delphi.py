"""Probe of the Delphi Epidata FluView endpoint for revision history.

FluView returns, for one epidemiological week, every published version ("issue") with its
``release_date``. This module samples a few weeks per year, collects their versions, and runs
the week-0 gate on them. Only the standard library is used, and the network call is injected
so tests run offline.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from datetime import date

from vintage_lakehouse.ingestion.epiweek import epiweek_to_date
from vintage_lakehouse.ingestion.errors import FetchError
from vintage_lakehouse.ingestion.gate import GateReport, ProbeMeta, VersionedPoint, evaluate

SOURCE_ID = "delphi-fluview"
BASE_URL = "https://api.delphi.cmu.edu/epidata/fluview/"
USER_AGENT = "vintage-lakehouse-gate"
SAMPLE_WEEKS = (5, 25)
MAX_ISSUES_AHEAD = 20  # keeps epiweek + issues_ahead inside the same year (week <= 30)

JsonFetcher = Callable[[str], object]


def http_get_json(url: str, *, timeout: float = 60.0) -> object:
    """GET ``url`` and parse the JSON body, mapping failures to ``FetchError``."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} for {url}", status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise FetchError(f"request failed for {url}: {exc}") from exc


def sample_epiweeks(years: Iterable[int]) -> list[int]:
    """Pick the sampled epiweeks (early and mid season) for each year."""
    return [year * 100 + week for year in years for week in SAMPLE_WEEKS]


def build_url(epiweek: int, *, region: str = "nat", issues_ahead: int = MAX_ISSUES_AHEAD) -> str:
    """URL asking for one epiweek across its first ``issues_ahead`` weekly issues."""
    if not 0 < issues_ahead <= MAX_ISSUES_AHEAD:
        raise ValueError(f"issues_ahead must be in 1..{MAX_ISSUES_AHEAD}")
    week = epiweek % 100
    if week + issues_ahead > 52:
        raise ValueError(f"epiweek {epiweek} + {issues_ahead} issues would cross a year boundary")
    return (
        f"{BASE_URL}?regions={region}&epiweeks={epiweek}&issues={epiweek}-{epiweek + issues_ahead}"
    )


def parse_fluview(payload: object) -> list[VersionedPoint]:
    """Turn a FluView response into versioned points (value is weighted ILI)."""
    if not isinstance(payload, dict):
        raise FetchError("unexpected response: not a JSON object")
    result = payload.get("result")
    if result == -2:  # the API's "no results" code
        return []
    if result != 1:
        raise FetchError(f"API error: {payload.get('message')!r}")
    rows = payload.get("epidata")
    if not isinstance(rows, list):
        raise FetchError("unexpected response: 'epidata' is not a list")

    points: list[VersionedPoint] = []
    for row in rows:
        if not isinstance(row, dict):
            raise FetchError("unexpected response: row is not an object")
        value = row.get("wili")
        if value is None:
            continue
        points.append(
            VersionedPoint(
                event_time=epiweek_to_date(int(row["epiweek"])),
                knowledge_time=date.fromisoformat(str(row["release_date"])),
                value=float(value),
            )
        )
    return points


def probe_fluview(
    *,
    years: Iterable[int] = range(2019, 2026),
    region: str = "nat",
    fetch: JsonFetcher = http_get_json,
    pause_seconds: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[VersionedPoint], ProbeMeta]:
    """Fetch the sampled epiweeks and record how the source behaved.

    HTTP 401/403 marks the source as needing credentials and 429 as throttled; both stop the
    probe and return what was collected so far. Other failures raise ``FetchError``.
    """
    points: list[VersionedPoint] = []
    requests = 0
    auth_required = False
    throttled = False
    started = clock()
    for index, epiweek in enumerate(sample_epiweeks(years)):
        if index:
            sleep(pause_seconds)
        requests += 1
        try:
            payload = fetch(build_url(epiweek, region=region))
        except FetchError as exc:
            if exc.status in (401, 403):
                auth_required = True
                break
            if exc.status == 429:
                throttled = True
                break
            raise
        points.extend(parse_fluview(payload))
    meta = ProbeMeta(
        requests=requests,
        elapsed_seconds=clock() - started,
        auth_required=auth_required,
        throttled=throttled,
    )
    return points, meta


def run_gate(*, fetch: JsonFetcher = http_get_json, pause_seconds: float = 0.5) -> GateReport:
    """Probe FluView and evaluate it against the gate criteria."""
    points, meta = probe_fluview(fetch=fetch, pause_seconds=pause_seconds)
    return evaluate(SOURCE_ID, points, meta)
