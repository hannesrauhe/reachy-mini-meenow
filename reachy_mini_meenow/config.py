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
    save_dir: Path | None
    viewer_port: int
    head_teach: bool
    head_teach_timeout_s: float
    selfie_zoom: float
    clock_trigger: bool
    clock_tick_s: float
    clock_up_deg: float | None
    # Voice commands: left antenna push-to-talk -> smart-home flows.
    voice: bool
    flows_url: str
    llm_base_url: str
    llm_api_key: str | None
    llm_model: str
    stt_provider: str
    whisper_server_url: str
    whisper_server_bin: str
    whisper_cli_bin: str
    whisper_model: str
    whisper_lang: str
    whisper_threads: int
    mistral_api_key: str | None
    mistral_stt_model: str
    piper_bin: str
    piper_voice: str | None
    voice_max_record_s: float
    audio_input_device: int | None
    audio_output_device: int | None


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

    # Antenna gestures (torque-released while idle): one antenna = capture,
    # both = teach the head. Threshold is how far a push counts as a deflection.
    touch_raw = os.environ.get("MEENOW_TOUCH_TRIGGER", "").strip()
    touch_trigger = _truthy(touch_raw) if touch_raw else True
    touch_threshold_deg = _float_env("MEENOW_TOUCH_THRESHOLD_DEG", 20.0)

    state_env = os.environ.get("MEENOW_STATE_FILE", "").strip()
    state_file = Path(state_env) if state_env else _default_state_file()

    # When set, every capture writes its JPEGs here (back/front/composite) so
    # the shots can be inspected without posting — most useful with DRY_RUN.
    save_dir_env = os.environ.get("MEENOW_SAVE_DIR", "").strip()
    save_dir = Path(save_dir_env).expanduser() if save_dir_env else None
    viewer_port = int(_float_env("MEENOW_VIEWER_PORT", 8899))

    # Head teach mode: hold both antennas down to make the head soft (gravity
    # comp), move it, then raise the antennas to store the new neutral pose.
    # Set false to disable. Requires the daemon on the Placo kinematics engine.
    teach_raw = os.environ.get("MEENOW_HEAD_TEACH", "").strip()
    head_teach = _truthy(teach_raw) if teach_raw else True
    # Idle time in teach mode before the head drifts back to the old neutral.
    head_teach_timeout_s = max(1.0, _float_env("MEENOW_HEAD_TEACH_TIMEOUT_S", 10.0))

    # Digital zoom on the selfie: center-crop by this factor, resize back.
    selfie_zoom = max(1.0, _float_env("MEENOW_SELFIE_ZOOM", 1.0))

    # Clock-antenna capture: wind the right antenna down, it ticks back up to 12
    # and takes the photo. Set false to fall back to the one-antenna tap trigger.
    clock_raw = os.environ.get("MEENOW_CLOCK", "").strip()
    clock_trigger = _truthy(clock_raw) if clock_raw else True
    # Seconds per countdown tick — one per second, five ticks per clock hour.
    clock_tick_s = max(0.2, _float_env("MEENOW_CLOCK_TICK_S", 1.0))
    # The 12-o'clock (resting) angle in degrees; unset uses the SDK up pose.
    clock_up_raw = os.environ.get("MEENOW_CLOCK_UP_DEG", "").strip()
    try:
        clock_up_deg = float(clock_up_raw) if clock_up_raw else None
    except ValueError:
        clock_up_deg = None

    # --- Voice commands (left antenna push-to-talk -> smart-home flows) ------
    voice_raw = os.environ.get("MEENOW_VOICE", "").strip()
    voice = _truthy(voice_raw) if voice_raw else False
    flows_url = os.environ.get(
        "MEENOW_FLOWS_URL", "http://raspi.fritz.box:8080"
    ).strip().rstrip("/")
    llm_base_url = os.environ.get(
        "MEENOW_LLM_BASE_URL", "http://localhost:11436/v1"
    ).strip().rstrip("/")
    llm_api_key = os.environ.get("MEENOW_LLM_API_KEY", "").strip() or None
    llm_model = os.environ.get("MEENOW_LLM_MODEL", "mistral-small-latest").strip()
    # STT: whisper_cpp (resident whisper-server) | whisper_cli | mistral.
    stt_provider = os.environ.get("MEENOW_STT_PROVIDER", "whisper_cpp").strip()
    # Default whisper.cpp location: the sibling checkout in this workspace.
    _ws_whisper = Path(__file__).resolve().parents[2] / "whisper.cpp"
    whisper_server_url = os.environ.get(
        "MEENOW_WHISPER_SERVER_URL", "http://127.0.0.1:8180"
    ).strip().rstrip("/")
    whisper_server_bin = os.environ.get(
        "MEENOW_WHISPER_SERVER_BIN", str(_ws_whisper / "build" / "bin" / "whisper-server")
    ).strip()
    whisper_cli_bin = os.environ.get(
        "MEENOW_WHISPER_CPP_BIN", str(_ws_whisper / "build" / "bin" / "whisper-cli")
    ).strip()
    whisper_model = os.environ.get(
        "MEENOW_WHISPER_MODEL", str(_ws_whisper / "models" / "ggml-large-v3-turbo-german.bin")
    ).strip()
    whisper_lang = os.environ.get("MEENOW_WHISPER_LANG", "de").strip() or "de"
    whisper_threads = max(1, int(_float_env("MEENOW_WHISPER_THREADS", 8)))
    mistral_api_key = os.environ.get("MEENOW_MISTRAL_API_KEY", "").strip() or None
    # Voxtral Mini Transcribe 2 (26.02). The old "voxtral-mini"/25.07 alias is deprecated.
    mistral_stt_model = os.environ.get("MEENOW_MISTRAL_STT_MODEL", "voxtral-mini-2602").strip()

    # One key for both Mistral jobs: reuse it for the LLM, but only when the
    # LLM endpoint really is Mistral — never leak it to a local/other host.
    if llm_api_key is None and mistral_api_key and "mistral.ai" in llm_base_url:
        llm_api_key = mistral_api_key

    # TTS: local piper CLI with a German voice model (unset = no voice, beeps).
    piper_bin = os.environ.get("MEENOW_PIPER_BIN", "piper").strip() or "piper"
    piper_voice = os.environ.get("MEENOW_PIPER_VOICE", "").strip() or None

    voice_max_record_s = max(1.0, _float_env("MEENOW_VOICE_MAX_RECORD_S", 15.0))

    def _int_env(name: str) -> int | None:
        raw = os.environ.get(name, "").strip()
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    audio_input_device = _int_env("MEENOW_AUDIO_INPUT_DEVICE")
    audio_output_device = _int_env("MEENOW_AUDIO_OUTPUT_DEVICE")

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
        save_dir=save_dir,
        viewer_port=viewer_port,
        head_teach=head_teach,
        head_teach_timeout_s=head_teach_timeout_s,
        selfie_zoom=selfie_zoom,
        clock_trigger=clock_trigger,
        clock_tick_s=clock_tick_s,
        clock_up_deg=clock_up_deg,
        voice=voice,
        flows_url=flows_url,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        stt_provider=stt_provider,
        whisper_server_url=whisper_server_url,
        whisper_server_bin=whisper_server_bin,
        whisper_cli_bin=whisper_cli_bin,
        whisper_model=whisper_model,
        whisper_lang=whisper_lang,
        whisper_threads=whisper_threads,
        mistral_api_key=mistral_api_key,
        mistral_stt_model=mistral_stt_model,
        piper_bin=piper_bin,
        piper_voice=piper_voice,
        voice_max_record_s=voice_max_record_s,
        audio_input_device=audio_input_device,
        audio_output_device=audio_output_device,
    )


def resolve_tz(tz_name: str | None):
    """Return a ``tzinfo`` for ``tz_name`` (IANA), or ``None`` for naive local time."""
    if not tz_name:
        return None
    from zoneinfo import ZoneInfo

    return ZoneInfo(tz_name)
