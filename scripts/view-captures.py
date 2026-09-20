#!/usr/bin/env python3
"""Serve the newest robot capture locally for quick visual checks.

Companion to MEENOW_SAVE_DIR: point it at the captures directory and open the
printed URL — the page always shows the composite from the most recent capture
folder and auto-refreshes, so another dry run appears without touching anything.
(The app starts this same server itself when MEENOW_SAVE_DIR is set; this
standalone mode is for browsing captures without running the bot.)

Usage:
    python scripts/view-captures.py [CAPTURES_DIR] [--port 8899] [--refresh 3]

CAPTURES_DIR defaults to ./captures (the .env.example suggestion). Only files
inside that directory are served; nothing external is linked.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from reachy_mini_meenow.capture_server import start_capture_server, stop_capture_server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("captures_dir", nargs="?", default="captures",
                    help="directory written by MEENOW_SAVE_DIR (default: ./captures)")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--refresh", type=int, default=3,
                    help="page auto-refresh interval in seconds (default: 3)")
    args = ap.parse_args()

    root = Path(args.captures_dir).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"error: {root} is not a directory (set MEENOW_SAVE_DIR?)")

    srv = start_capture_server(root, args.port, args.refresh)
    if srv is None:
        raise SystemExit(f"error: could not bind port {args.port}")
    try:
        input("Ctrl+C to stop\n")
    except KeyboardInterrupt:
        pass
    finally:
        stop_capture_server(srv)


if __name__ == "__main__":
    main()
