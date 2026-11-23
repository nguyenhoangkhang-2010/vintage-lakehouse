"""Canned Delphi FluView responses for offline tests."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from vintage_lakehouse.ingestion.epiweek import epiweek_to_date


def row(
    epiweek: int, release: str, wili: float | None = 4.0, issue: int | None = None
) -> dict[str, Any]:
    return {
        "release_date": release,
        "region": "nat",
        "issue": issue or epiweek,
        "epiweek": epiweek,
        "wili": wili,
    }


def payload(rows: list[dict[str, Any]], *, result: int = 1, message: str = "success") -> bytes:
    return json.dumps({"result": result, "message": message, "epidata": rows}).encode("utf-8")


def year_response(
    year: int, *, issues: int = 4, drift: float = 0.1, value_shift: float = 0.0
) -> bytes:
    """Weeks 1 to 3 of ``year``, each published ``issues`` times.

    Weeks 1 and 2 drift upwards by ``drift`` with every issue (so they are revised); week 3 keeps
    the same value (so its later issues are unchanged republications). For ``issues=4`` that is
    12 rows: 3 new periods, 6 revisions and 3 unchanged. ``value_shift`` changes every value,
    to simulate a source that rewrites its history.
    """
    rows: list[dict[str, Any]] = []
    for week in (1, 2, 3):
        epiweek = year * 100 + week
        start = epiweek_to_date(epiweek)
        for k in range(issues):
            release = (start + timedelta(days=7 * (k + 1) + 5)).isoformat()
            wili = 1.0 + week + value_shift + (drift * k if week != 3 else 0.0)
            rows.append(row(epiweek, release, wili, issue=epiweek + k))
    return payload(rows)


def fake_fetch(**options: Any) -> Callable[[str], bytes]:
    """Return a fetcher that answers any FluView URL with ``year_response`` for its year."""

    def fetch(url: str) -> bytes:
        match = re.search(r"epiweeks=(\d{4})", url)
        assert match is not None, url
        return year_response(int(match.group(1)), **options)

    return fetch
