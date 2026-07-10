"""meenow Reachy Mini app.

At the daily meenow trigger time the robot performs a get-ready gesture, captures a
photo, posts it to a dedicated Pixelfed account as a followers-only ``#meenowApp``
status, and celebrates. Followers then see the photo in the meenow PWA feed.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from reachy_mini import ReachyMini, ReachyMiniApp

from . import camera, gestures
from .config import Config, load_config, resolve_tz
from .pixelfed import PixelfedClient, build_status_text
from .state import load_posted_trigger_ms, save_posted_trigger_ms
from .trigger import TriggerClock

log = logging.getLogger(__name__)

_POLL_MS = 1000
_ERROR_BACKOFF_MS = 30_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _interruptible_sleep(total_ms: int, stop_event: threading.Event) -> None:
    slept = 0
    while slept < total_ms and not stop_event.is_set():
        step = min(_POLL_MS, total_ms - slept)
        time.sleep(step / 1000)
        slept += step


class MeenowApp(ReachyMiniApp):
    """Reachy Mini Apps entry point for the meenow daily-photo app."""

    custom_app_url = None  # config is environment-variable only; no web UI
    request_media_backend = "default"  # opt into camera access for capture

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

        try:
            if cfg.post_now:
                log.info("MEENOW_POST_NOW set: firing one capture immediately.")
                trigger_ms = clock.last_trigger_ms(_now_ms())
                url = self._capture_and_post(cfg, client, reachy_mini, stop_event)
                posted_ms = trigger_ms
                save_posted_trigger_ms(cfg.state_file, trigger_ms, post_url=url)

            while not stop_event.is_set():
                trigger_ms = clock.last_trigger_ms(_now_ms())
                fresh = posted_ms != trigger_ms
                within_catchup = (
                    cfg.catchup_min <= 0
                    or _now_ms() < trigger_ms + cfg.catchup_min * 60_000
                )
                if fresh and within_catchup:
                    try:
                        url = self._capture_and_post(
                            cfg, client, reachy_mini, stop_event
                        )
                        posted_ms = trigger_ms
                        save_posted_trigger_ms(cfg.state_file, trigger_ms, post_url=url)
                    except Exception as exc:  # noqa: BLE001 - keep the loop alive
                        log.error("Post attempt failed, will retry: %s", exc)
                        _interruptible_sleep(_ERROR_BACKOFF_MS, stop_event)
                        continue
                elif fresh:
                    # Missed this period's window; adopt it so we wait for the next.
                    posted_ms = trigger_ms
                    log.info(
                        "Trigger %d already outside the %d-min window; waiting for next.",
                        trigger_ms, cfg.catchup_min,
                    )
                _interruptible_sleep(_POLL_MS, stop_event)
        finally:
            gestures.go_neutral(reachy_mini)

    def _capture_and_post(self, cfg: Config, client, reachy_mini,
                          stop_event: threading.Event) -> str | None:
        gestures.get_ready(reachy_mini, stop_event)
        frame = camera.capture_frame(
            reachy_mini, allow_synthetic=cfg.dry_run or cfg.allow_synthetic
        )
        jpeg = camera.encode_jpeg(frame)
        if cfg.dry_run or client is None:
            log.info(
                "DRY_RUN would POST visibility=private, %d bytes, status=%r",
                len(jpeg), build_status_text(cfg.caption),
            )
            url = None
        else:
            url = client.post_photo(jpeg, cfg.caption, alt="meenow — daily photo")
            log.info("Posted: %s", url)
        gestures.celebrate(reachy_mini, stop_event)
        return url


def main() -> None:
    # "default" (LOCAL IPC camera) on the real robot; "no_media" for headless
    # testing without a camera (the capture pipeline uses a synthetic frame).
    MeenowApp.request_media_backend = os.environ.get("MEENOW_MEDIA_BACKEND", "default")
    app = MeenowApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()


if __name__ == "__main__":
    main()
