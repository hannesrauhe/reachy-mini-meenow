"""meenow Reachy Mini app.

At the daily meenow trigger time the robot performs a get-ready gesture, captures a
surroundings photo, turns to a side mirror for a selfie, stitches the two like the
meenow PWA (selfie inset on the surroundings shot), and posts the composite plus both
source photos to a dedicated Pixelfed account as a followers-only ``#meenowApp``
status, then celebrates. Followers see the photos in the meenow PWA feed.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from reachy_mini import ReachyMini, ReachyMiniApp

from . import camera, gestures
from .config import Config, load_config, resolve_tz
from .pixelfed import PixelfedClient, build_status_text
from .state import load_posted_trigger_ms, save_posted_trigger_ms
from .trigger import TriggerClock
from .trigger_touch import AntennaTrigger

log = logging.getLogger(__name__)

_POLL_MS = 1000
_ERROR_BACKOFF_MS = 30_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _save_jpegs(save_dir: Path, back: bytes, front: bytes, composite: bytes) -> None:
    """Write the three JPEGs to ``save_dir`` for inspection (e.g. after a dry run).

    One timestamped subdirectory per capture keeps runs apart; a save failure is
    logged but never fails the post — the photos are a debugging convenience.
    """
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = save_dir / stamp
        out.mkdir(parents=True, exist_ok=True)
        for name, data in (("back", back), ("front", front), ("composite", composite)):
            (out / f"{name}.jpg").write_bytes(data)
        log.info("Saved photos to %s", out)
    except OSError as exc:  # noqa: BLE001 - saving must never break the post
        log.warning("Could not save photos to %s: %s", save_dir, exc)


def _interruptible_sleep(total_ms: int, stop_event: threading.Event) -> None:
    slept = 0
    while slept < total_ms and not stop_event.is_set():
        step = min(_POLL_MS, total_ms - slept)
        time.sleep(step / 1000)
        slept += step


def _wait_or_touch(total_ms: int, stop_event: threading.Event,
                   trigger: AntennaTrigger | None) -> bool:
    """Sleep like ``_interruptible_sleep`` but poll the antenna trigger; True if it fired."""
    step_s = 0.2
    end = time.monotonic() + total_ms / 1000
    while time.monotonic() < end and not stop_event.is_set():
        if trigger is not None and trigger.check():
            return True
        time.sleep(step_s)
    return False


class MeenowApp(ReachyMiniApp):
    """Reachy Mini Apps entry point for the meenow daily-photo app."""

    custom_app_url = None  # config is environment-variable only; no web UI
    # Default: release the camera so we capture directly (see camera.py). Set
    # MEENOW_MEDIA_BACKEND=default to use the SDK's WebRTC/LOCAL media stream instead.
    request_media_backend = "no_media"

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
        cfg = load_config()
        clock = TriggerClock(resolve_tz(cfg.tz))
        client = None if cfg.dry_run else PixelfedClient(cfg.instance, cfg.token)

        if client is not None:
            account_id = client.verify_credentials()
            log.info("Posting as account id=%s on %s", account_id, cfg.instance)
        else:
            log.info("DRY_RUN: no Pixelfed credentials required.")

        posted_ms = load_posted_trigger_ms(cfg.state_file)
        touch = AntennaTrigger(reachy_mini, cfg.touch_threshold_deg) if cfg.touch_trigger else None

        try:
            if cfg.post_now:
                log.info("MEENOW_POST_NOW set: firing one capture immediately.")
                trigger_ms = clock.last_trigger_ms(_now_ms())
                url = self._capture_and_post(cfg, client, reachy_mini, stop_event)
                posted_ms = trigger_ms
                save_posted_trigger_ms(cfg.state_file, trigger_ms, post_url=url)
            else:
                gestures.hello(reachy_mini, stop_event)
                log.info("meenow ready — wiggle an antenna to post.")

            if touch is not None and not touch.arm():
                touch = None

            while not stop_event.is_set():
                trigger_ms = clock.last_trigger_ms(_now_ms())
                fresh = posted_ms != trigger_ms
                within_catchup = (
                    cfg.catchup_min <= 0
                    or _now_ms() < trigger_ms + cfg.catchup_min * 60_000
                )
                if fresh and within_catchup:
                    try:
                        if touch is not None:
                            touch.disarm()
                        url = self._capture_and_post(
                            cfg, client, reachy_mini, stop_event
                        )
                        posted_ms = trigger_ms
                        save_posted_trigger_ms(cfg.state_file, trigger_ms, post_url=url)
                    except Exception as exc:  # noqa: BLE001 - keep the loop alive
                        log.error("Post attempt failed, will retry: %s", exc)
                        _interruptible_sleep(_ERROR_BACKOFF_MS, stop_event)
                        continue
                    finally:
                        if touch is not None:
                            touch.arm()
                elif fresh:
                    # Missed this period's window; adopt it so we wait for the next.
                    posted_ms = trigger_ms
                    log.info(
                        "Trigger %d already outside the %d-min window; waiting for next.",
                        trigger_ms, cfg.catchup_min,
                    )
                if _wait_or_touch(_POLL_MS, stop_event, touch):
                    log.info("Manual antenna trigger: capturing now.")
                    touch.disarm()
                    try:
                        url = self._capture_and_post(
                            cfg, client, reachy_mini, stop_event
                        )
                        # An extra post is fine; only mark a not-yet-posted period.
                        if posted_ms != trigger_ms:
                            posted_ms = trigger_ms
                            save_posted_trigger_ms(cfg.state_file, trigger_ms, post_url=url)
                    except Exception as exc:  # noqa: BLE001 - keep the loop alive
                        log.error("Manual post failed: %s", exc)
                        _interruptible_sleep(_ERROR_BACKOFF_MS, stop_event)
                    finally:
                        touch.arm()
        finally:
            if touch is not None:
                touch.disarm()
            gestures.go_neutral(reachy_mini)

    def _capture_and_post(self, cfg: Config, client, reachy_mini,
                          stop_event: threading.Event) -> str | None:
        gestures.get_ready(reachy_mini, stop_event)
        devices = camera.resolve_devices(
            cfg.camera_device, auto=cfg.media_backend == "no_media"
        )
        allow_synthetic = cfg.dry_run or cfg.allow_synthetic
        back = camera.capture_frame(
            reachy_mini, allow_synthetic=allow_synthetic, devices=devices
        )
        if stop_event.is_set():
            return None

        # Selfie via the mirror to the right: turn body+head 90° right, tilt down.
        gestures.look_at_mirror(
            reachy_mini, stop_event,
            yaw_deg=cfg.mirror_yaw_deg, pitch_deg=cfg.mirror_pitch_deg,
        )
        try:
            front = camera.capture_frame(
                reachy_mini, allow_synthetic=allow_synthetic, devices=devices
            )
        finally:
            gestures.go_neutral(reachy_mini)

        composite = camera.stitch_photos(back, front, flip_front=cfg.mirror_flip)
        composite_jpeg = camera.encode_jpeg(composite)
        back_jpeg = camera.encode_jpeg(back)
        front_jpeg = camera.encode_jpeg(front)
        if cfg.save_dir is not None:
            _save_jpegs(cfg.save_dir, back_jpeg, front_jpeg, composite_jpeg)
        if cfg.dry_run or client is None:
            log.info(
                "DRY_RUN would POST visibility=private, %d+%d+%d bytes, status=%r",
                len(composite_jpeg), len(back_jpeg), len(front_jpeg),
                build_status_text(cfg.caption),
            )
            url = None
        else:
            url = client.post_meenow(composite_jpeg, back_jpeg, front_jpeg, cfg.caption)
            log.info("Posted: %s", url)
        gestures.celebrate(reachy_mini, stop_event)
        return url


def main() -> None:
    # Default "no_media": the daemon releases the camera and we capture directly.
    # "default" re-enables the SDK's WebRTC/LOCAL media stream as the source.
    MeenowApp.request_media_backend = os.environ.get("MEENOW_MEDIA_BACKEND", "no_media")
    app = MeenowApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()


if __name__ == "__main__":
    main()
