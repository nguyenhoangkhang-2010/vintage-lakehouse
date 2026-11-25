"""Tests of ``vintage-lakehouse serve`` on a small stored series (the server loop is replaced)."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from backfill_fixture import SERIES, revised_series, varying_truth
from vintage_lakehouse import api
from vintage_lakehouse.api import command
from vintage_lakehouse.cli import main


def seed_store(data_dir: Path) -> None:
    pytest.importorskip("vintage_lakehouse.storage.iceberg")
    from vintage_lakehouse.storage.iceberg import IcebergStore

    store = IcebergStore.local(data_dir / "warehouse")
    try:
        store.append(revised_series(40, varying_truth))
    finally:
        store.close()


def argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "serve",
        "--data-dir",
        str(tmp_path / "data"),
        "--series",
        SERIES,
        "--port",
        "0",
        *extra,
    ]


def test_serve_starts_a_server_that_answers_and_stops_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_store(tmp_path / "data")
    answers: list[tuple[int, int]] = []

    def fake_serve(server: ThreadingHTTPServer) -> None:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/series", timeout=10) as reply:
            assert json.loads(reply.read()) == {"series": [SERIES]}
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as reply:
            answers.append((reply.status, len(reply.read())))
        server.shutdown()
        thread.join(timeout=5)

    monkeypatch.setattr(command, "serve_forever", fake_serve)
    assert main(argv(tmp_path)) == 0
    out = capsys.readouterr()
    assert "serving 1 series read-only at http://localhost:" in out.out
    assert "warning" not in out.err
    assert answers and answers[0][0] == 200 and answers[0][1] > 1000


def test_ctrl_c_stops_the_server_and_exits_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_store(tmp_path / "data")

    def interrupted(server: ThreadingHTTPServer) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(command, "serve_forever", interrupted)
    assert main(argv(tmp_path)) == 0
    assert "stopped" in capsys.readouterr().out


def test_binding_beyond_this_machine_prints_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_store(tmp_path / "data")
    monkeypatch.setattr(command, "serve_forever", lambda server: None)
    assert main(argv(tmp_path, "--host", "0.0.0.0")) == 0
    assert "no authentication" in capsys.readouterr().err


def test_a_series_without_data_exits_with_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_store(tmp_path / "data")
    assert main(argv(tmp_path, "--series", "no/such")) == 2
    assert "no data found" in capsys.readouterr().err


@pytest.mark.parametrize("port", ["-1", "70000"])
def test_a_bad_port_exits_with_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], port: str
) -> None:
    seed_store(tmp_path / "data")
    assert main(argv(tmp_path, "--port", port)) == 2
    assert "port" in capsys.readouterr().err


def test_a_port_in_use_exits_with_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_store(tmp_path / "data")

    def in_use(*args: object, **kwargs: object) -> ThreadingHTTPServer:
        raise OSError("address already in use")

    monkeypatch.setattr(command, "make_server", in_use)
    assert main(argv(tmp_path)) == 2
    assert "cannot listen" in capsys.readouterr().err


def test_the_package_exports_the_api() -> None:
    assert set(api.__all__) == {"Api", "Response", "make_server"}
