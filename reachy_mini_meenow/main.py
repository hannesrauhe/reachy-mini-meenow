"""meenow Reachy Mini app.

At the daily meenow trigger time the robot performs a get-ready gesture, captures a
surroundings photo, turns to a side mirror for a selfie, stitches the two like the
meenow PWA (selfie inset on the surroundings shot), and posts the composite plus both
source photos to a dedicated Pixelfed account as a followers-only ``#meenowApp``
status. Followers see the photos in the meenow PWA feed.
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
from .antenna_gestures import ONE, UP, AntennaGesture
from .capture_server import start_capture_server, stop_capture_server
from .config import Config, load_config, resolve_tz
from .head_teacher import LOCKED, HeadTeacher
from .pixelfed import PixelfedClient, build_status_text
from .state import (
    load_neutral_pose,
    load_posted_trigger_ms,
    save_neutral_pose,
    save_posted_trigger_ms,
)
from .trigger import TriggerClock

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


def _wait_gesture(total_ms: int, stop_event: threading.Event,
                  gesture: AntennaGesture | None,
                  teacher: HeadTeacher | None) -> bool:
    """Sleep ~``total_ms`` in steps, driving the teacher and watching for capture.

    Each step polls the antenna gesture and feeds it to the head teacher (so the
    teach state machine advances even while we idle). Returns True when the
    single-antenna capture gesture fires — but only while the head is LOCKED, so
    the transient one-antenna reading while *raising* out of teach mode is not
    mistaken for a capture.
    """
    step_s = 0.2
    end = time.monotonic() + total_ms / 1000
    while time.monotonic() < end and not stop_event.is_set():
        g = gesture.poll() if gesture is not None else UP
        if teacher is not None:
            teacher.update(g)
        if g == ONE and (teacher is None or teacher.state == LOCKED):
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
        gesture = AntennaGesture(reachy_mini, cfg.touch_threshold_deg) if cfg.touch_trigger else None
        teacher = (
            HeadTeacher(
                reachy_mini,
                neutral_pose=load_neutral_pose(cfg.state_file),
                timeout_s=cfg.head_teach_timeout_s,
                on_store=lambda pose: save_neutral_pose(cfg.state_file, pose),
                # After teach ends the antennas were stiffened by gravity comp;
                # perk them up and re-baseline the reader so they go soft again
                # and a held-down pair cannot instantly re-trigger teach.
                rearm=(lambda: gesture.arm() if gesture is not None else None),
            )
            if cfg.head_teach
            else None
        )
        # The viewer shows the taught neutral pose; the teacher owns it in
        # memory (fresh after every store), the state file covers teach-off.
        viewer = (
            start_capture_server(
                cfg.save_dir,
                cfg.viewer_port,
                get_pose=(
                    lambda: teacher.neutral_pose
                    if teacher is not None
                    else load_neutral_pose(cfg.state_file)
                ),
            )
            if cfg.save_dir
            else None
        )

        try:
            if cfg.post_now:
                log.info("MEENOW_POST_NOW set: firing one capture immediately.")
                trigger_ms = clock.last_trigger_ms(_now_ms())
                url = self._capture_and_post(cfg, client, reachy_mini, stop_event, teacher=teacher)
                posted_ms = trigger_ms
                save_posted_trigger_ms(cfg.state_file, trigger_ms, post_url=url)
            else:
                gestures.hello(reachy_mini, stop_event)
                log.info("meenow ready — one antenna = post, both = teach the head.")

            if gesture is not None and not gesture.arm():
                gesture = None

            while not stop_event.is_set():
                trigger_ms = clock.last_trigger_ms(_now_ms())
                fresh = posted_ms != trigger_ms
                within_catchup = (
                    cfg.catchup_min <= 0
                    or _now_ms() < trigger_ms + cfg.catchup_min * 60_000
                )
                if fresh and within_catchup:
                    try:
                        if gesture is not None:
                            gesture.disarm()
                        url = self._capture_and_post(
                            cfg, client, reachy_mini, stop_event, teacher=teacher
                        )
                        posted_ms = trigger_ms
                        save_posted_trigger_ms(cfg.state_file, trigger_ms, post_url=url)
                    except Exception as exc:  # noqa: BLE001 - keep the loop alive
                        log.error("Post attempt failed, will retry: %s", exc)
                        _interruptible_sleep(_ERROR_BACKOFF_MS, stop_event)
                        continue
                    finally:
                        if gesture is not None:
                            gesture.arm()
                elif fresh:
                    # Missed this period's window; adopt it so we wait for the next.
                    posted_ms = trigger_ms
                    log.info(
                        "Trigger %d already outside the %d-min window; waiting for next.",
                        trigger_ms, cfg.catchup_min,
                    )
                if _wait_gesture(_POLL_MS, stop_event, gesture, teacher):
                    log.info("Manual capture (one antenna): capturing now.")
                    gesture.disarm()
                    try:
                        url = self._capture_and_post(
                            cfg, client, reachy_mini, stop_event, teacher=teacher
                        )
                        # An extra post is fine; only mark a not-yet-posted period.
                        if posted_ms != trigger_ms:
                            posted_ms = trigger_ms
                            save_posted_trigger_ms(cfg.state_file, trigger_ms, post_url=url)
                    except Exception as exc:  # noqa: BLE001 - keep the loop alive
                        log.error("Manual post failed: %s", exc)
                        _interruptible_sleep(_ERROR_BACKOFF_MS, stop_event)
                    finally:
                        gesture.arm()
        finally:
            if teacher is not None:
                teacher.suspend_for_capture()
            stop_capture_server(viewer)
            if gesture is not None:
                gesture.disarm()
            gestures.go_neutral(reachy_mini)

    def _capture_and_post(self, cfg: Config, client, reachy_mini,
                          stop_event: threading.Event, *,
                          teacher: HeadTeacher | None = None) -> str | None:
        if teacher is not None:
            # Scripted motion below needs position control; drop out of any soft
            # teach window first (the pose being shaped is discarded).
            teacher.suspend_for_capture()
        gestures.get_ready(
            reachy_mini, stop_event,
            # Hold the taught neutral (not the absolute one) for the first shot.
            hold_pose=(
                (lambda _r, duration=0.5: teacher.go_to_neutral(duration))
                if teacher is not None
                else None
            ),
        )
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
            # Return to the taught neutral (world-neutral if none taught).
            if teacher is not None:
                teacher.go_to_neutral()
            else:
                gestures.go_neutral(reachy_mini)
        front = camera.zoom(front, cfg.selfie_zoom)

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
