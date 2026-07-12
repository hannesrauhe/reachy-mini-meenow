"""Camera capture and JPEG encoding.

Grabs a frame from the robot's camera (BGR ``numpy`` array). When the camera is
unavailable — common in a headless simulator without a rendering backend — a
synthetic placeholder frame is used instead so the capture-and-post pipeline stays
exercisable end to end.
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np

log = logging.getLogger(__name__)

_JPEG_QUALITY = 92  # matches meenow's canvas.toBlob('image/jpeg', 0.92)


def capture_frame(reachy_mini, *, allow_synthetic: bool, device: str | None = None) -> np.ndarray:
    """Return a BGR ``uint8`` frame.

    Tries, in order: the SDK media stream (``reachy_mini.media.get_frame()``); a
    direct camera device via OpenCV when ``device`` is set (used with
    ``MEENOW_MEDIA_BACKEND=no_media`` on hosts where the WebRTC media pipeline is
    unavailable, e.g. a Raspberry Pi where the audio chain fails to negotiate); and
    finally a synthetic placeholder when ``allow_synthetic`` is set.
    """
    media = getattr(reachy_mini, "media", None)
    if media is not None:
        try:
            frame = media.get_frame()
            if frame is not None and np.asarray(frame).any():
                return np.asarray(frame, dtype=np.uint8)
            log.warning("Camera stream returned an empty frame.")
        except Exception as exc:  # noqa: BLE001 - defensive: camera backends vary
            log.warning("Camera stream failed: %s", exc)

    if device is not None:
        log.info("Trying direct camera capture from device %s", device)
        frame = capture_from_device(device)
        if frame is not None and frame.size:
            return np.asarray(frame, dtype=np.uint8)
        log.warning("Direct capture from device %s failed.", device)

    if allow_synthetic:
        log.info("Using synthetic placeholder frame.")
        return synthetic_frame()
    raise RuntimeError(
        "Camera unavailable (media stream empty and no working MEENOW_CAMERA_DEVICE) "
        "and synthetic frames are not allowed."
    )


def _parse_device(device: str):
    """Return an int index for numeric strings, else the path/string as-is."""
    s = str(device).strip()
    return int(s) if s.isdigit() else s


def capture_from_device(device: str, warmup: int = 5):
    """Grab a BGR frame directly from a camera via OpenCV, or ``None`` on failure.

    Reads a few frames to let auto-exposure settle. Intended for use after the
    daemon has released the camera (``MEENOW_MEDIA_BACKEND=no_media`` triggers the
    SDK's ``release_media()``), so the device is free for direct access.
    """
    try:
        import cv2
    except ImportError:
        log.warning("OpenCV not available for direct capture.")
        return None
    cap = cv2.VideoCapture(_parse_device(device))
    if not cap.isOpened():
        cap.release()
        return None
    try:
        frame = None
        for _ in range(max(1, warmup)):
            ok, f = cap.read()
            if ok and f is not None and getattr(f, "size", 0):
                frame = f
        return frame
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
