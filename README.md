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

1. performs a short "get-ready" gesture and faces the camera,
2. captures a surroundings photo,
3. turns body and head 90° to the right (towards a physical mirror placed next to
   the robot) and slightly down, and captures a mirror selfie,
4. stitches the two like the meenow PWA (selfie as a rounded inset on the
   surroundings shot),
5. posts the composite plus both source photos to a **dedicated Pixelfed account**
   as a followers-only (`visibility: private`) status tagged `#meenowApp`, and
6. plays a small celebratory gesture.

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

## Configuration

All configuration is via environment variables (a `.env` file is loaded if present).
See [`.env.example`](.env.example). Required (unless `MEENOW_DRY_RUN=true`):

| Variable | Purpose |
|---|---|
| `MEENOW_PIXELFED_INSTANCE` | Dedicated account's Pixelfed host (bare, no scheme) |
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

> The dedicated account must be **locked** (manually approve followers) for the
> photos to stay followers-only; approve your meenow friends from Pixelfed or the
> meenow Circle screen.

## Install & run

```bash
python -m venv .venv && . .venv/bin/activate
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
