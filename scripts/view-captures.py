#!/usr/bin/env python3
"""Serve the newest robot capture locally for quick visual checks.

Companion to MEENOW_SAVE_DIR: point it at the captures directory and open the
printed URL — the page always shows the composite from the most recent capture
folder and auto-refreshes, so another dry run appears without touching anything.

Usage:
    python scripts/view-captures.py [CAPTURES_DIR] [--port 8899] [--refresh 3]

CAPTURES_DIR defaults to ./captures (the .env.example suggestion). Only files
inside that directory are served; nothing external is linked.
"""

from __future__ import annotations

import argparse
import html
import http.server
import socketserver
from pathlib import Path

INDEX = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>meenow — latest capture</title>
<meta http-equiv="refresh" content="{refresh}">
<style>
  body {{ font-family: system-ui, sans-serif; background: #1a1a1a; color: #eee;
         margin: 1.5rem; }}
  h1 {{ font-size: 1.1rem; color: #f5c542; }}
  img {{ max-width: 100%; max-height: 85vh; border-radius: 8px; display: block; }}
  p.hint {{ color: #888; font-size: .85rem; }}
</style>
</head>
<body>
<h1>{title}</h1>
{body}
</body>
</html>
"""


def latest_capture(root: Path) -> Path | None:
    """Newest subdirectory containing composite.jpg, by name (timestamp-prefixed)."""
    dirs = [d for d in root.iterdir() if d.is_dir() and (d / "composite.jpg").is_file()]
    return max(dirs, key=lambda d: d.name) if dirs else None


def make_handler(root: Path, refresh_s: int) -> type[http.server.SimpleHTTPRequestHandler]:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def do_GET(self):  # noqa: N802 - http.server API
            if self.path not in ("/", "/index.html"):
                super().do_GET()
                return
            latest = latest_capture(root)
            if latest is None:
                title = "meenow — no captures yet"
                body = '<p class="hint">Run the bot with MEENOW_SAVE_DIR set, ' \
                       "then refresh this page.</p>"
            else:
                title = f"Latest composite — {html.escape(latest.name)}"
                body = f'<img src="{latest.name}/composite.jpg" alt="latest composite">'
            page = INDEX.format(refresh=refresh_s, title=title, body=body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):  # keep the console quiet
            pass

    return Handler


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

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    with Server(("127.0.0.1", args.port), make_handler(root, args.refresh)) as srv:
        print(f"Serving {root}\nOpen http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
        srv.serve_forever()


if __name__ == "__main__":
    main()
