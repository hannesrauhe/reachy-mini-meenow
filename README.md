---
title: Reachy Mini meenow
emoji: 📸
colorFrom: yellow
colorTo: pink
sdk: static
pinned: false
tags:
  - reachy_mini
  - reachy_mini_python_app
---

# reachy-mini-meenow

A [Reachy Mini](https://github.com/pollen-robotics/reachy_mini) app that turns the
daily [meenow](https://meenow.de) photo ritual into something the robot does on its
own. At the same pseudo-random daily **trigger time** the meenow PWA prompts its
users, the robot:

1. faces the camera and captures a surroundings photo,
2. turns body and head 90° to the right (towards a physical mirror placed next to
   the robot) and slightly down, and captures a mirror selfie,
3. stitches the two like the meenow PWA (selfie as a rounded inset on the
   surroundings shot),
4. posts the composite plus both source photos to a **dedicated Pixelfed account**
   as a followers-only (`visibility: private`) status tagged `#meenowApp`, and
5. slowly returns to its neutral pose with the antennas back up.

In scheduled mode the robot waves hello on startup so you can see the app is
running. While it waits, the antennas perk up once and are then torque-released
(they feel loose) and act as controls:

- **wind the right antenna down** — the bot's right antenna is a clock hand, with
  12 o'clock at the resting (up) pose. Wind it counter-clockwise to an hour (11,
  10, 9, 8...) and hold it still for a moment, and it ticks back up to 12 **once
  per second** (five ticks per hour) — the photo is taken when it reaches 12.
  Winding to 8 o'clock is a 20-second countdown. Push it back down
  mid-countdown to cancel. (The extra capture also posts, and counts as the
  period's post when the scheduled one has not fired yet.)
- **hold both antennas down** — the head goes *soft* (gravity compensation): move
  it wherever you like, then **raise the antennas** to store that as the new
  **neutral** pose — the one the robot returns to after every selfie. If you
  move nothing for ~10 s instead, it drifts back to the old neutral and locks.

Teach mode needs the daemon on the Placo kinematics engine
(`reachy-mini-daemon --kinematics-engine Placo`); without it the head simply
stays locked and the gesture is ignored. Set `MEENOW_HEAD_TEACH=false` to
disable it entirely.

Followers of that account then see the daily photo in their meenow feed — the app
reuses meenow's exact trigger math and post format, so no changes to meenow are
needed.

## How it works

- **Trigger time** — ported bit-for-bit from meenow's `src/trigger-core.mjs`
  (`reachy_mini_meenow/trigger.py`): a date-seeded value places the trigger
  somewhere in the 09:00–21:00 local window. By default the host's local time is
  used, matching a co-located meenow install; set `MEENOW_TZ` to pin an IANA zone.
- **Posting** — mirrors meenow's `src/api/pixelfed.ts`: upload media
  (`POST /api/v1/media`, polling until processing completes), then create the status
  (`POST /api/v1/statuses`, `visibility: private`, `#meenowApp`). Requests use HTTP/2
  (via httpx): Pixelfed tokens are large JWTs, and some instances' edge (e.g.
  gram.social) reject that uncompressed `Authorization` header over HTTP/1.1 with a
  400 — HTTP/2 HPACK-compresses it. httpx falls back to HTTP/1.1 where HTTP/2 is
  unavailable.
- **Once per period** — the trigger epoch of the last posted period is persisted, so
  a restart within the same period does not post twice. A missed window is skipped
  (see `MEENOW_CATCHUP_MINUTES`).
- **Clock-antenna capture** — the right antenna doubles as a clock hand
  (`reachy_mini_meenow/clock_antenna.py`): its joint angle maps to a clock hour
  (12 = the resting up pose), a wound-down position held still for a second
  commits the countdown, and the hand then steps up one hour per interval until
  it strikes 12 and the photo fires. It is a plain state machine driven from the
  poll loop (no thread, so it can't race a capture), and it only winds while the
  left antenna is up — so *both down* stays the teach gesture. Set
  `MEENOW_CLOCK=false` to fall back to the plain one-antenna tap trigger.
- **Antenna gestures** — the torque-released antennas are read as a two-way
  switch (`reachy_mini_meenow/antenna_gestures.py`): their present position is
  compared to the perk-up baseline and classified `up` / `one` / `both`, so a
  single reader can never confuse the capture gesture (one) with the teach
  gesture (both). Readings are debounced over two polls.
- **Head teach mode** — a LOCKED/TEACH state machine
  (`reachy_mini_meenow/head_teacher.py`) driven from the poll loop (no
  background thread, so it can't race a capture). Both antennas down enables
  gravity compensation (soft, floats where you leave it — needs the Placo
  engine); raising them stores the current head pose as the new neutral
  (persisted, joint-space so the body never follows) and locks it; ~10 s of no
  movement instead returns to the old neutral. The neutral is what the robot
  returns to after the selfie.

## Configuration

All configuration is via environment variables (a `.env` file is loaded if present).
See [`.env.example`](.env.example). Required (unless `MEENOW_DRY_RUN=true`):

| Variable | Purpose |
|---|---|
| `MEENOW_PIXELFED_INSTANCE` | Dedicated account's Pixelfed host (bare, no scheme) — should be meenow's home instance, `pixelfed.social`, so robot posts are local to the community |
| `MEENOW_PIXELFED_TOKEN` | Access token for that account (needs the `write` scope) |
| `MEENOW_TZ` | *(optional)* IANA timezone; defaults to host local time |
| `MEENOW_CAPTION` | *(optional)* text prepended above the `#meenowApp` tag |
| `MEENOW_CATCHUP_MINUTES` | *(optional)* late-post window, default 120; `0` = always |
| `MEENOW_DRY_RUN` | *(dev)* log the post instead of sending |
| `MEENOW_POST_NOW` | *(dev)* fire one capture immediately, ignoring the schedule |
| `MEENOW_CAMERA_DEVICE` | *(optional)* camera device to capture from (`/dev/videoN` or index); auto-detected by default |
| `MEENOW_MEDIA_BACKEND` | *(optional)* `no_media` (default, direct capture) or `default` (SDK WebRTC/LOCAL media stream) |
| `MEENOW_ALLOW_SYNTHETIC` | *(dev)* allow the placeholder frame in a real post (test without a camera) |
| `MEENOW_MIRROR_YAW_DEG` | *(optional)* body/head yaw for the mirror selfie, default `-90` (negative = right) |
| `MEENOW_MIRROR_PITCH_DEG` | *(optional)* head pitch for the mirror selfie, default `10` (positive = down) |
| `MEENOW_MIRROR_FLIP` | *(optional)* horizontally un-mirror the selfie, default `true` |
| `MEENOW_SELFIE_ZOOM` | *(optional)* digital zoom on the selfie (center-crop factor ≥ 1), default `1` |
| `MEENOW_HEAD_TEACH` | *(optional)* antenna-gated head teach mode, default `true` (needs daemon `--kinematics-engine Placo`) |
| `MEENOW_HEAD_TEACH_TIMEOUT_S` | *(optional)* idle time in teach mode before returning to the old neutral, default `10` |
| `MEENOW_CLOCK` | *(optional)* clock-antenna capture (wind the right antenna down, it ticks up to 12 = photo), default `true` |
| `MEENOW_CLOCK_TICK_S` | *(optional)* seconds per tick, default `1` — five ticks per hour, so winding to 8 = 20 s countdown |
| `MEENOW_CLOCK_UP_DEG` | *(optional)* the 12 o'clock angle in degrees; defaults to the SDK up pose (−10) |
| `MEENOW_TOUCH_TRIGGER` | *(optional)* antenna gestures (one = capture, both = teach) — the capture trigger only when `MEENOW_CLOCK=false`, default `true` |
| `MEENOW_TOUCH_THRESHOLD_DEG` | *(optional)* how far a push counts as an antenna deflection, default `20` |
| `MEENOW_SAVE_DIR` | *(optional)* also write each capture's `back.jpg` / `front.jpg` / `composite.jpg` into a timestamped subfolder here (works with `MEENOW_DRY_RUN` to inspect shots without posting). When set, the app also serves the newest composite on localhost — see below |
| `MEENOW_VIEWER_PORT` | *(optional)* port for the capture viewer, default `8899` (localhost only) |

> The dedicated account must be **locked** (manually approve followers) for the
> photos to stay followers-only; approve your meenow friends from Pixelfed or the
> meenow Circle screen.

## Install & run

```bash
python -m venv .reachy-venv && . .reachy-venv/bin/activate
pip install -e .                 # app + runtime deps
pip install -e ".[sim]"          # + MuJoCo simulator, for development without a robot
```

The app connects to a running **daemon**. Start it (real robot or simulation):

```bash
# Simulation (no hardware). MUJOCO_GL=egl (GPU) or osmesa (no GPU) for headless.
MUJOCO_GL=egl reachy-mini-daemon --sim        # see `reachy-mini-daemon --help`
```

Then run the app:

```bash
# Dry run — no network, synthetic frame if the camera is unavailable:
MEENOW_DRY_RUN=true MEENOW_POST_NOW=true reachy-mini-meenow

# Real single post (fill in .env first):
MEENOW_POST_NOW=true reachy-mini-meenow

# Scheduled mode — waits for the daily trigger:
reachy-mini-meenow
```

In production the app is discovered via its `reachy_mini_apps` entry point and
launched from the robot dashboard.

### Viewing captures locally

Set `MEENOW_SAVE_DIR` and the app starts a small localhost viewer as part of
the run — open `http://127.0.0.1:8899/` (port via `MEENOW_VIEWER_PORT`) and the
page shows the composite from the newest capture folder, auto-refreshing so the
next run appears without restarting anything:

```bash
MEENOW_DRY_RUN=true MEENOW_POST_NOW=true MEENOW_SAVE_DIR=./captures reachy-mini-meenow
```

To browse captures without running the bot, the same server is available
standalone:

```bash
scripts/view-captures.py ./captures
```

It serves only files from the captures directory (localhost only) — no external
links.

## Autostart on boot (Raspberry Pi)

[`scripts/start-on-boot.sh`](scripts/start-on-boot.sh) activates the venv,
starts `reachy-mini-daemon` (skipped when one is already reachable on
`localhost:8000`), waits for it to come up, and runs the app. The virtualenv
is auto-detected in the repo root (`.reachy_mini_env`, then `.venv`; override
with `MEENOW_VENV`). All output goes to stdout/stderr; `.env` is picked up
from the repo root as usual.

The recommended way to run it is the systemd unit
[`scripts/meenow.service`](scripts/meenow.service): journald captures and
rotates the logs (the daemon logs a lot — tune `SystemMaxUse=` in
`/etc/systemd/journald.conf` if needed) and the app restarts after a crash.
Adjust `User=` and the path in `ExecStart=`, then:

```
sudo cp scripts/meenow.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meenow.service   # start now + on every boot
journalctl -u meenow -f                      # follow the logs
```

`systemctl stop meenow` shuts daemon and app down cleanly (the script's exit
trap stops the daemon it started). Alternatively, from crontab — pipe to
syslog since cron discards output:

```
@reboot /home/pi/reachy-mini-meenow/scripts/start-on-boot.sh 2>&1 | logger -t meenow
```

Paths are overridable via `MEENOW_HOME`, `MEENOW_VENV`, `MEENOW_DAEMON_ARGS`
(e.g. `--sim`), and `MEENOW_DAEMON_URL`.

## Running on a Raspberry Pi (aarch64)

meenow captures the photo **directly** from the local camera (auto-detecting the
Reachy Mini video device) and does not use the SDK's WebRTC media stream, so it runs
on a Pi out of the box — no camera configuration and no GStreamer WebRTC plugin
required for this app. See [`docs/gstreamer-webrtc-arm64.md`](docs/gstreamer-webrtc-arm64.md)
for details, the optional `MEENOW_CAMERA_DEVICE` override, and how to build the
WebRTC plugin off-device if you run **other** Reachy Mini apps that need it.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

`tests/test_trigger.py` verifies the trigger-math parity (including the 32-bit
masking regression that would otherwise cluster every June-2026 day at ~16:50).
`tests/test_pixelfed.py` verifies the media/status request shapes.
`tests/test_camera.py` verifies device auto-detection, the capture fallback order,
and the stitch geometry.
