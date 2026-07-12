"""Camera capture and JPEG encoding.

The app needs a single still photo, so it captures **directly** from the local
camera device (OpenCV / V4L2) rather than the SDK's WebRTC media pipeline. The
WebRTC path is fragile on low-power hosts — on a Raspberry Pi its bidirectional
audio chain can fail to negotiate and take the video stream down with it — and it
is unnecessary overhead for a one-frame operation.

Direct capture requires the daemon to have released the camera: the app sets
``request_media_backend = "no_media"`` by default, which triggers the SDK's
``release_media()`` so the device is free. The Reachy Mini camera device is
auto-detected (by its V4L2 name); ``MEENOW_CAMERA_DEVICE`` overrides it. Setting
``MEENOW_MEDIA_BACKEND=default`` re-enables the SDK media stream as the source.
"""

from __future__ import annotations

import glob
import logging
import os
import time
from datetime import datetime

import numpy as np

log = logging.getLogger(__name__)

_JPEG_QUALITY = 92  # matches meenow's canvas.toBlob('image/jpeg', 0.92)
_CAMERA_NAME_HINTS = ("reachy", "mini")


def _video_device_names() -> dict[str, str]:
    """Map ``/dev/videoN`` → V4L2 device name (Linux; empty on other platforms)."""
    out: dict[str, str] = {}
    for name_path in sorted(glob.glob("/sys/class/video4linux/video*/name")):
        try:
            with open(name_path, encoding="utf-8") as fh:
                name = fh.read().strip()
        except OSError:
            continue
        dev = "/dev/" + os.path.basename(os.path.dirname(name_path))
        out[dev] = name
    return out


def detect_reachy_camera() -> list[str]:
    """Candidate ``/dev/videoN`` nodes whose V4L2 name looks like the Reachy camera.

    Returns them lowest-index first; the metadata node (e.g. ``video1``) simply
    yields no frames and is skipped by ``capture_frame``.
    """
    names = _video_device_names()
    matches = [d for d, n in names.items() if any(h in n.lower() for h in _CAMERA_NAME_HINTS)]
    return sorted(matches)


def resolve_devices(explicit: str | None, auto: bool) -> list[str]:
    """Devices to try: an explicit override, else auto-detection when enabled."""
    if explicit:
        return [explicit]
    return detect_reachy_camera() if auto else []


def capture_frame(
    reachy_mini, *, allow_synthetic: bool, devices: list[str] | None = None
) -> np.ndarray:
    """Return a BGR ``uint8`` frame.

    Tries each local ``devices`` entry (OpenCV), then the SDK media stream
    (``reachy_mini.media.get_frame()``, only populated when
    ``MEENOW_MEDIA_BACKEND=default``), then a synthetic placeholder when
    ``allow_synthetic`` is set.
    """
    for dev in devices or []:
        log.info("Capturing from camera device %s", dev)
        frame = capture_from_device(dev)
        if frame is not None and frame.size:
            return np.asarray(frame, dtype=np.uint8)
        log.warning("Capture from device %s failed.", dev)

    media = getattr(reachy_mini, "media", None)
    if media is not None:
        try:
            frame = media.get_frame()
            if frame is not None and np.asarray(frame).any():
                return np.asarray(frame, dtype=np.uint8)
        except Exception as exc:  # noqa: BLE001 - defensive: camera backends vary
            log.warning("SDK media stream failed: %s", exc)

    if allow_synthetic:
        log.info("Using synthetic placeholder frame.")
        return synthetic_frame()
    raise RuntimeError(
        "No camera frame available (no working device, SDK media stream empty) "
        "and synthetic frames are not allowed. Set MEENOW_CAMERA_DEVICE or "
        "MEENOW_MEDIA_BACKEND=default."
    )


def _parse_device(device: str):
    """Return an int index for numeric strings, else the path/string as-is."""
    s = str(device).strip()
    return int(s) if s.isdigit() else s


def capture_from_device(device: str, settle_s: float = 2.5, min_std: float = 5.0):
    """Grab a BGR frame directly from a camera via OpenCV, or ``None`` on failure.

    A freshly (re)opened USB camera streams blank/greyscale frames for a moment
    while it starts up and auto-exposure settles — especially on a Pi after the
    daemon released it. So we read for up to ``settle_s`` seconds and return the
    first 3-channel frame whose pixel spread (``std``) clears ``min_std`` (i.e. an
    actual image, not a flat grey placeholder), keeping the best frame seen as a
    fallback. A single-channel frame is promoted to BGR so the JPEG is not encoded
    greyscale.
    """
    try:
        import cv2
    except ImportError:
        log.warning("OpenCV not available for direct capture.")
        return None

    dev = _parse_device(device)
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2) if isinstance(dev, str) else cv2.VideoCapture(dev)
    if not cap.isOpened():
        cap.release()
        return None
    try:
        best = None
        best_std = -1.0
        deadline = time.monotonic() + max(0.1, settle_s)
        while time.monotonic() < deadline:
            ok, f = cap.read()
            if ok and f is not None and getattr(f, "size", 0):
                std = float(f.std())
                if std > best_std:
                    best, best_std = f, std
                if f.ndim == 3 and f.shape[2] == 3 and std >= min_std:
                    return f
            time.sleep(0.05)
        if best is None:
            return None
        if best.ndim == 2 or (best.ndim == 3 and best.shape[2] == 1):
            best = cv2.cvtColor(best.reshape(best.shape[0], best.shape[1]), cv2.COLOR_GRAY2BGR)
        log.warning("Camera frame looked flat (std=%.1f); using best available.", best_std)
        return best
    finally:
        cap.release()


def synthetic_frame(size: tuple[int, int] = (480, 640)) -> np.ndarray:
    """A recognisable dev placeholder: gradient background + timestamp label."""
    h, w = size
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :, 0] = np.linspace(40, 200, w, dtype=np.uint8)  # blue ramp (BGR)
    frame[:, :, 2] = np.linspace(200, 40, h, dtype=np.uint8)[:, None]  # red ramp
    label = "meenow synthetic " + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        import cv2

        cv2.putText(
            frame, label, (16, h - 24), cv2.FONT_HERSHEY_SIMPLEX,
            0.7, (255, 255, 255), 2, cv2.LINE_AA,
        )
    except ImportError:
        pass
    return frame


def encode_jpeg(bgr: np.ndarray, quality: int = _JPEG_QUALITY) -> bytes:
    """Encode a BGR frame to JPEG bytes (OpenCV, with a PIL fallback)."""
    try:
        import cv2

        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("cv2.imencode failed")
        return buf.tobytes()
    except ImportError:
        from io import BytesIO

        from PIL import Image

        rgb = bgr[:, :, ::-1]  # BGR -> RGB
        out = BytesIO()
        Image.fromarray(rgb).save(out, format="JPEG", quality=quality)
        return out.getvalue()
