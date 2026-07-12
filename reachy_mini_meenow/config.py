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
    allow_synthetic: bool
    media_backend: str
    camera_device: str | None
    catchup_min: int
    state_file: Path
    mirror_yaw_deg: float
    mirror_pitch_deg: float
    mirror_flip: bool
    touch_trigger: bool
    touch_threshold_deg: float


def load_config() -> Config:
    load_dotenv()

    instance = _normalise_instance(os.environ.get("MEENOW_PIXELFED_INSTANCE", ""))
    token = os.environ.get("MEENOW_PIXELFED_TOKEN", "").strip()
    tz = os.environ.get("MEENOW_TZ", "").strip() or None
    caption = os.environ.get("MEENOW_CAPTION", "").strip() or None
    dry_run = _truthy(os.environ.get("MEENOW_DRY_RUN"))
    post_now = _truthy(os.environ.get("MEENOW_POST_NOW"))
    allow_synthetic = _truthy(os.environ.get("MEENOW_ALLOW_SYNTHETIC"))
    media_backend = os.environ.get("MEENOW_MEDIA_BACKEND", "no_media").strip() or "no_media"
    camera_device = os.environ.get("MEENOW_CAMERA_DEVICE", "").strip() or None

    catchup_raw = os.environ.get("MEENOW_CATCHUP_MINUTES", "120").strip()
    try:
        catchup_min = int(catchup_raw)
    except ValueError:
        catchup_min = 120

    def _float_env(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, "").strip() or default)
        except ValueError:
            return default

    # Mirror-selfie pose: negative yaw turns right, positive pitch tilts down.
    mirror_yaw_deg = _float_env("MEENOW_MIRROR_YAW_DEG", -90.0)
    mirror_pitch_deg = _float_env("MEENOW_MIRROR_PITCH_DEG", 10.0)
    mirror_flip_raw = os.environ.get("MEENOW_MIRROR_FLIP", "").strip()
    mirror_flip = _truthy(mirror_flip_raw) if mirror_flip_raw else True

    # Manual trigger: wiggle a torque-released antenna to fire a capture.
    touch_raw = os.environ.get("MEENOW_TOUCH_TRIGGER", "").strip()
    touch_trigger = _truthy(touch_raw) if touch_raw else True
    touch_threshold_deg = _float_env("MEENOW_TOUCH_THRESHOLD_DEG", 20.0)

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
        allow_synthetic=allow_synthetic,
        media_backend=media_backend,
        camera_device=camera_device,
        catchup_min=catchup_min,
        state_file=state_file,
        mirror_yaw_deg=mirror_yaw_deg,
        mirror_pitch_deg=mirror_pitch_deg,
        mirror_flip=mirror_flip,
        touch_trigger=touch_trigger,
        touch_threshold_deg=touch_threshold_deg,
    )


def resolve_tz(tz_name: str | None):
    """Return a ``tzinfo`` for ``tz_name`` (IANA), or ``None`` for naive local time."""
    if not tz_name:
        return None
    from zoneinfo import ZoneInfo

    return ZoneInfo(tz_name)
