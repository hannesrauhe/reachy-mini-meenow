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


def capture_frame(reachy_mini, *, allow_synthetic: bool) -> np.ndarray:
    """Return a BGR ``uint8`` frame. Falls back to a synthetic frame when allowed."""
    media = getattr(reachy_mini, "media", None)
    if media is not None:
        try:
            frame = media.get_frame()
            if frame is not None and np.asarray(frame).any():
                return np.asarray(frame, dtype=np.uint8)
            log.warning("Camera returned an empty frame.")
        except Exception as exc:  # noqa: BLE001 - defensive: camera backends vary
            log.warning("Camera capture failed: %s", exc)

    if allow_synthetic:
        log.info("Using synthetic placeholder frame.")
        return synthetic_frame()
    raise RuntimeError("Camera unavailable and synthetic frames are not allowed.")


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
