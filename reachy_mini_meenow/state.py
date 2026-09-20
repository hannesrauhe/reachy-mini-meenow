"""Persisted app state: post idempotency and the taught neutral head pose.

One JSON file holds two independent keys, read-modify-written so neither
clobbers the other:

- ``posted_trigger_ms`` — trigger epoch (ms) of the most recently posted period,
  so a restart within the same period does not post twice. Mirrors the meenow
  PWA's ``posted-trigger-ms`` IndexedDB key.
- ``neutral_pose`` — the head joint pose (7 floats, rad) the user last taught by
  hand; the robot returns here after each selfie.
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
_memory: dict = {}


def _read_payload(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return dict(_memory)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read state file %s: %s", path, exc)
        return dict(_memory)


def _write_payload(path: Path, payload: dict) -> None:
    _memory.clear()
    _memory.update(payload)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning(
            "Could not persist state to %s: %s (state degraded to memory)",
            path, exc,
        )


def load_posted_trigger_ms(path: Path) -> int | None:
    value = _read_payload(path).get("posted_trigger_ms")
    return int(value) if value is not None else None


def save_posted_trigger_ms(
    path: Path, trigger_ms: int, *, post_url: str | None = None
) -> None:
    payload = _read_payload(path)
    payload["posted_trigger_ms"] = trigger_ms
    payload["post_url"] = post_url
    _write_payload(path, payload)


def load_neutral_pose(path: Path) -> list[float] | None:
    """The taught neutral head pose (7 floats, rad), or None if never taught."""
    pose = _read_payload(path).get("neutral_pose")
    if isinstance(pose, list) and len(pose) == 7:
        return [float(v) for v in pose]
    return None


def save_neutral_pose(path: Path, pose: list[float]) -> None:
    payload = _read_payload(path)
    payload["neutral_pose"] = [float(v) for v in pose]
    _write_payload(path, payload)
