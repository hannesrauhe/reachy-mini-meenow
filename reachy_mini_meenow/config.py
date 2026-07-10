"""Environment-variable configuration.

All runtime configuration comes from environment variables (optionally via a
``.env`` file). See ``.env.example`` for the full list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional at runtime
    def load_dotenv(*_a, **_k):  # type: ignore
        return False


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalise_instance(raw: str) -> str:
    """Strip scheme and trailing slash; every API URL interpolates a bare host."""
    host = raw.strip()
    for prefix in ("https://", "http://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix):]
            break
    return host.rstrip("/")


def _default_state_file() -> Path:
    try:
        from platformdirs import user_state_dir

        return Path(user_state_dir("reachy_mini_meenow")) / "state.json"
    except ImportError:
        return Path.home() / ".local" / "state" / "reachy_mini_meenow" / "state.json"


@dataclass(frozen=True)
class Config:
    instance: str
    token: str
    tz: str | None
    caption: str | None
    dry_run: bool
    post_now: bool
    catchup_min: int
    state_file: Path


def load_config() -> Config:
    load_dotenv()

    instance = _normalise_instance(os.environ.get("MEENOW_PIXELFED_INSTANCE", ""))
    token = os.environ.get("MEENOW_PIXELFED_TOKEN", "").strip()
    tz = os.environ.get("MEENOW_TZ", "").strip() or None
    caption = os.environ.get("MEENOW_CAPTION", "").strip() or None
    dry_run = _truthy(os.environ.get("MEENOW_DRY_RUN"))
    post_now = _truthy(os.environ.get("MEENOW_POST_NOW"))

    catchup_raw = os.environ.get("MEENOW_CATCHUP_MINUTES", "120").strip()
    try:
        catchup_min = int(catchup_raw)
    except ValueError:
        catchup_min = 120

    state_env = os.environ.get("MEENOW_STATE_FILE", "").strip()
    state_file = Path(state_env) if state_env else _default_state_file()

    if not dry_run:
        missing = [
            name
            for name, val in (
                ("MEENOW_PIXELFED_INSTANCE", instance),
                ("MEENOW_PIXELFED_TOKEN", token),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                "Missing required environment variable(s): "
                + ", ".join(missing)
                + " (set MEENOW_DRY_RUN=true to run without posting)."
            )

    return Config(
        instance=instance,
        token=token,
        tz=tz,
        caption=caption,
        dry_run=dry_run,
        post_now=post_now,
        catchup_min=catchup_min,
        state_file=state_file,
    )


def resolve_tz(tz_name: str | None):
    """Return a ``tzinfo`` for ``tz_name`` (IANA), or ``None`` for naive local time."""
    if not tz_name:
        return None
    from zoneinfo import ZoneInfo

    return ZoneInfo(tz_name)
