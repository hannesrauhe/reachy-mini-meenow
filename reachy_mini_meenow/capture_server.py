"""Local capture viewer: serves the newest ``MEENOW_SAVE_DIR`` composite over HTTP.

Started automatically by the app when ``MEENOW_SAVE_DIR`` is set (port via
``MEENOW_VIEWER_PORT``), and available standalone through ``scripts/view-captures.py``.
Binds to localhost only and serves only files inside the captures directory —
no external links, nothing reachable from the network.
"""

from __future__ import annotations

import html
import http.server
import logging
import socketserver
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

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
  p.pose {{ color: #9ad; font-size: .85rem; margin-top: .8rem; }}
</style>
</head>
<body>
<h1>{title}</h1>
{body}
<p class="pose">{pose}</p>
</body>
</html>
"""


def format_pose(pose: list[float] | None) -> str:
    """Human-readable summary of a neutral head pose (joint-space, rad).

    Layout: [body_yaw, neck_1..4, stiffness] — the Stewart-platform joints are
    opaque angles, so only body yaw is spelled out; the rest go as raw radians.
    """
    if not pose:
        return "Neutral pose: none taught yet (hold both antennas down to teach one)"
    names = ("body yaw", "neck 1", "neck 2", "neck 3", "neck 4", "stiffness")
    parts = [f"{n} {v:+.3f}" for n, v in zip(names, pose[:6])]
    return "Neutral pose (rad): " + ", ".join(parts)


def _default_pose() -> list[float] | None:
    from .config import load_config
    from .state import load_neutral_pose

    return load_neutral_pose(load_config().state_file)


def latest_capture(root: Path) -> Path | None:
    """Newest subdirectory containing composite.jpg, by name (timestamp-prefixed)."""
    dirs = [d for d in root.iterdir() if d.is_dir() and (d / "composite.jpg").is_file()]
    return max(dirs, key=lambda d: d.name) if dirs else None


def make_handler(
    root: Path,
    refresh_s: int,
    get_pose: Callable[[], list[float] | None] | None = None,
) -> type[http.server.SimpleHTTPRequestHandler]:
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
                body = ('<p class="hint">Run the bot with MEENOW_SAVE_DIR set, '
                        "then refresh this page.</p>")
            else:
                title = f"Latest composite — {html.escape(latest.name)}"
                body = f'<img src="{latest.name}/composite.jpg" alt="latest composite">'
            try:
                pose_text = format_pose((get_pose or _default_pose)())
            except Exception:  # noqa: BLE001 - the pose line must never break the page
                pose_text = "Neutral pose: unavailable"
            page = INDEX.format(
                refresh=refresh_s, title=title, body=body, pose=html.escape(pose_text)
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # keep the bot's console quiet

    return Handler


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_capture_server(
    root: Path,
    port: int,
    refresh_s: int = 3,
    get_pose: Callable[[], list[float] | None] | None = None,
) -> _Server | None:
    """Serve ``root`` on 127.0.0.1:``port`` in a background thread.

    ``get_pose`` returns the current taught neutral head pose (or None) for the
    info line on the index page; it defaults to reading the app state file.

    Returns the server (pass to :func:`stop_capture_server`), or ``None`` if the
    port is unavailable — the viewer is a convenience and must never break the
    capture-and-post loop.
    """
    try:
        srv = _Server(("127.0.0.1", port), make_handler(root, refresh_s, get_pose))
    except OSError as exc:
        log.warning("Capture viewer not started (port %d): %s", port, exc)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Capture viewer: http://127.0.0.1:%d/ (serving %s)", port, root)
    return srv


def stop_capture_server(srv: _Server | None) -> None:
    if srv is None:
        return
    srv.shutdown()
    srv.server_close()
