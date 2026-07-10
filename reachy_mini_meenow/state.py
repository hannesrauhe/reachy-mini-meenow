"""Persisted post state for once-per-period idempotency.

Records the trigger epoch (ms) of the most recently posted period, so an app
restart within the same period does not post a second photo. Mirrors the meenow
PWA's ``posted-trigger-ms`` IndexedDB key.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# In-memory fallback used when the state directory is not writable, so the app
# still avoids double-posting within a single process even without persistence.
_memory: dict[str, int | None] = {"posted_trigger_ms": None}


def load_posted_trigger_ms(path: Path) -> int | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        value = data.get("posted_trigger_ms")
        return int(value) if value is not None else None
    except FileNotFoundError:
        return _memory["posted_trigger_ms"]
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read state file %s: %s", path, exc)
        return _memory["posted_trigger_ms"]


def save_posted_trigger_ms(
    path: Path, trigger_ms: int, *, post_url: str | None = None
) -> None:
    _memory["posted_trigger_ms"] = trigger_ms
    payload = {
        "posted_trigger_ms": trigger_ms,
        "post_url": post_url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning(
            "Could not persist state to %s: %s (restart-idempotency degraded)",
            path,
            exc,
        )
