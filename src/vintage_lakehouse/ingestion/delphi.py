"""Delphi Epidata FluView: the week-0 gate probe and the ingestion source.

FluView returns, for each epidemiological week, every published version ("issue") with its
``release_date``. The gate samples a few weeks per year and checks that revision history is
kept. The ingestion source pulls a full range of weeks, one request per year, and turns the rows
into observations.

Only the standard library is used, and every network call is injected so tests run offline.

The legacy endpoint used here is being phased out in favour of Delphi's V5 API (the API itself
reports that it serves no data after 2026-09-22). That is why ingestion archives every raw
response: see docs/adr/0006-archive-raw-responses.md.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from datetime import date
from typing import Any

from vintage_lakehouse.ingestion.epiweek import epiweek_to_date, weeks_in_year
from vintage_lakehouse.ingestion.errors import FetchError
from vintage_lakehouse.ingestion.gate import GateReport, ProbeMeta, VersionedPoint, evaluate
from vintage_lakehouse.ingestion.pipeline import Chunk
from vintage_lakehouse.storage.model import Observation, knowledge_time_from_date

SOURCE_ID = "delphi-fluview"
BASE_URL = "https://api.delphi.cmu.edu/epidata/fluview/"
USER_AGENT = "vintage-lakehouse"
SAMPLE_WEEKS = (5, 25)
MAX_ISSUES_AHEAD = 20  # keeps epiweek + issues_ahead inside the same year (week <= 30)

JsonFetcher = Callable[[str], object]
BytesFetcher = Callable[[str], bytes]


def http_get_bytes(url: str, *, timeout: float = 60.0) -> bytes:
    """GET ``url`` and return the body, mapping failures to ``FetchError``."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body: bytes = response.read()
            return body
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} for {url}", status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise FetchError(f"request failed for {url}: {exc}") from exc


def decode_payload(raw: bytes) -> object:
    """Parse a JSON body, mapping malformed input to ``FetchError``."""
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"response is not valid JSON: {exc}") from exc


def http_get_json(url: str, *, timeout: float = 60.0) -> object:
    """GET ``url`` and parse the JSON body."""
    return decode_payload(http_get_bytes(url, timeout=timeout))


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


def _fluview_rows(payload: object) -> list[dict[str, Any]]:
    """Validate a FluView response and return its rows."""
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
    checked: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise FetchError("unexpected response: row is not an object")
        checked.append(row)
    return checked


def _row_fields(row: dict[str, Any]) -> tuple[date, date, float] | None:
    """Return (event date, release date, weighted ILI) of a row, or None if it has no value."""
    value = row.get("wili")
    if value is None:
        return None
    try:
        return (
            epiweek_to_date(int(row["epiweek"])),
            date.fromisoformat(str(row["release_date"])),
            float(value),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FetchError(f"malformed row {row!r}: {exc}") from exc


def parse_fluview(payload: object) -> list[VersionedPoint]:
    """Turn a FluView response into versioned points (value is weighted ILI)."""
    points: list[VersionedPoint] = []
    for row in _fluview_rows(payload):
        fields = _row_fields(row)
        if fields is not None:
            event, released, value = fields
            points.append(VersionedPoint(event_time=event, knowledge_time=released, value=value))
    return points


def series_id_for(region: str) -> str:
    """Series id used for the weighted ILI of one FluView region."""
    return f"delphi/fluview/{region}/wili"


def parse_observations(payload: object, *, series_id: str) -> list[Observation]:
    """Turn a FluView response into observations.

    The source only states the release *date*, so ``knowledge_time`` is the start of the next
    day (see ``knowledge_time_from_date``).
    """
    observations: list[Observation] = []
    for row in _fluview_rows(payload):
        fields = _row_fields(row)
        if fields is None:
            continue
        event, released, value = fields
        try:
            observations.append(
                Observation(
                    series_id=series_id,
                    event_time=event,
                    knowledge_time=knowledge_time_from_date(released),
                    value=value,
                    source=SOURCE_ID,
                )
            )
        except ValueError as exc:
            raise FetchError(f"row cannot be stored {row!r}: {exc}") from exc
    return observations


class FluViewSource:
    """Pulls every published version of weighted ILI for a region, one request per year."""

    source_id = SOURCE_ID

    def __init__(
        self,
        *,
        region: str = "nat",
        fetch: BytesFetcher | None = None,
        pause_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.region = region
        self._fetch: BytesFetcher = fetch or http_get_bytes
        self._pause = pause_seconds
        self._sleep = sleep

    @property
    def series_id(self) -> str:
        """The series id this source writes to."""
        return series_id_for(self.region)

    def request_url(self, year: int, last_issue: int) -> str:
        """URL for every epiweek of ``year`` across all issues from that year to ``last_issue``."""
        weeks = f"{year}01-{year}{weeks_in_year(year):02d}"
        return f"{BASE_URL}?regions={self.region}&epiweeks={weeks}&issues={year}01-{last_issue}"

    def parse(self, raw: bytes) -> list[Observation]:
        """Parse one archived or fetched response into observations."""
        return parse_observations(decode_payload(raw), series_id=self.series_id)

    def chunks(self, first_year: int, last_year: int, last_issue: int) -> Iterator[Chunk]:
        """Yield one chunk per year of epiweeks, in order.

        ``last_issue`` (an epiweek such as ``202647``) bounds how late a version may have been
        published, which makes a pull reproducible. Years that start after it are skipped.
        """
        first = True
        for year in range(first_year, last_year + 1):
            if last_issue < year * 100 + 1:
                break
            if not first:
                self._sleep(self._pause)
            first = False
            url = self.request_url(year, last_issue)
            raw = self._fetch(url)
            yield Chunk(request=url, raw=raw, observations=self.parse(raw))


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
