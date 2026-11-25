"""The ``vintage-lakehouse serve`` command: the read-only API and the time-slider demo."""

from __future__ import annotations

import argparse
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

from vintage_lakehouse.api.app import FAR_FUTURE, Api, make_server
from vintage_lakehouse.config import Settings

DEFAULT_SERIES = "delphi/fluview/nat/wili"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the options of ``serve``."""
    parser.add_argument("--data-dir", type=Path, help="Where the warehouse lives.")
    parser.add_argument(
        "--series",
        action="append",
        help=f"A series to serve (repeatable; default {DEFAULT_SERIES}).",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Address to bind (default: local only)."
    )
    parser.add_argument("--port", type=int, default=8000, help="Port (0 picks a free one).")


def serve_forever(server: ThreadingHTTPServer) -> None:
    """Serve until interrupted (a seam for tests)."""
    server.serve_forever()


def run(args: argparse.Namespace) -> int:
    """Open the store, check the series have data, and serve."""
    if not 0 <= args.port <= 65535:
        print("invalid settings: port must be between 0 and 65535", file=sys.stderr)
        return 2
    try:
        from vintage_lakehouse.storage.iceberg import IcebergStore
    except ImportError:
        print(
            'the Iceberg store is not installed; run: pip install -e ".[iceberg]"', file=sys.stderr
        )
        return 2

    series = list(dict.fromkeys(args.series or [DEFAULT_SERIES]))
    data_dir = (args.data_dir or Settings.from_env().data_dir).resolve()
    store = IcebergStore.local(data_dir / "warehouse")
    try:
        empty = [s for s in series if not store.as_of(s, FAR_FUTURE)]
        if empty:
            print(
                f"no data found for series {empty[0]!r}: check the series and the data folder",
                file=sys.stderr,
            )
            return 2
        try:
            server = make_server(Api(store, series), args.host, args.port)
        except OSError as exc:
            print(f"cannot listen on {args.host}:{args.port}: {exc}", file=sys.stderr)
            return 2
        host, port = server.server_address[:2]
        if args.host not in LOOPBACK:
            print(
                "warning: this server has no authentication or encryption and is reachable "
                f"from the network at {args.host}",
                file=sys.stderr,
            )
        shown = "localhost" if args.host in LOOPBACK else str(host)
        print(f"serving {len(series)} series read-only at http://{shown}:{port}/  (Ctrl+C to stop)")
        try:
            serve_forever(server)
        except KeyboardInterrupt:
            print("stopped")
        finally:
            server.server_close()
    finally:
        store.close()
    return 0
