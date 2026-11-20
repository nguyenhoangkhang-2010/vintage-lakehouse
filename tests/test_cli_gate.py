"""Tests for the ``gate`` CLI command (the probe itself is replaced by a fake)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from vintage_lakehouse.cli import main
from vintage_lakehouse.ingestion import delphi
from vintage_lakehouse.ingestion.errors import FetchError
from vintage_lakehouse.ingestion.gate import GateReport, ProbeMeta, VersionedPoint, evaluate

META = ProbeMeta(requests=2, elapsed_seconds=1.0, auth_required=False, throttled=False)


def report_with(versions: int) -> GateReport:
    points = [
        VersionedPoint(
            event_time=date(2019, 1, 6) + timedelta(days=400 * i),
            knowledge_time=date(2019, 1, 6) + timedelta(days=400 * i + 7 * (v + 1)),
            value=1.0 + 0.1 * v,
        )
        for i in range(10)
        for v in range(versions)
    ]
    return evaluate("test-source", points, META)


def test_gate_prints_report_and_exits_zero_when_it_passes(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(delphi, "run_gate", lambda: report_with(5))
    assert main(["gate", "delphi-fluview"]) == 0
    out = capsys.readouterr().out
    assert "Gate report: test-source" in out
    assert "Criteria still to check by a human: 4" in out


def test_gate_exits_one_when_a_criterion_fails(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(delphi, "run_gate", lambda: report_with(1))
    assert main(["gate", "delphi-fluview"]) == 1
    assert "| 1 |" in capsys.readouterr().out


def test_gate_exits_two_when_the_source_cannot_be_reached(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable() -> GateReport:
        raise FetchError("network down")

    monkeypatch.setattr(delphi, "run_gate", unreachable)
    assert main(["gate", "delphi-fluview"]) == 2
    assert "gate could not run: network down" in capsys.readouterr().err


def test_gate_rejects_unknown_sources() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["gate", "no-such-source"])
    assert exc_info.value.code == 2
