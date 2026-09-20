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


def _to_bgr(cv2, f):
    """Normalise an OpenCV frame to 3-channel BGR, or ``None`` if not possible.

    Handles the raw formats the V4L2 backend can return when it does not convert:
    single-channel grey and 2-channel packed YUYV.
    """
    if f is None or not getattr(f, "size", 0):
        return None
    if f.ndim == 2:
        return cv2.cvtColor(f, cv2.COLOR_GRAY2BGR)
    if f.ndim == 3:
        c = f.shape[2]
        if c == 3:
            return f
        if c == 1:
            return cv2.cvtColor(f.reshape(f.shape[0], f.shape[1]), cv2.COLOR_GRAY2BGR)
        if c == 2:
            try:
                return cv2.cvtColor(f, cv2.COLOR_YUV2BGR_YUYV)
            except cv2.error:
                return None
    return None


def capture_from_device(device: str, settle_s: float = 3.0, min_std: float = 5.0,
                        skip_s: float = 0.6):
    """Grab a BGR frame directly from a camera via OpenCV, or ``None`` on failure.

    Forces RGB conversion (``CAP_PROP_CONVERT_RGB``) and requests **MJPG** at a fixed
    resolution so frames are decoded to colour — without this the V4L2 backend can
    hand back the raw luma plane, yielding a greyscale image. A freshly (re)opened
    USB camera also needs a moment for auto-exposure to converge, so we skip the
    first ``skip_s`` seconds, sample until ``settle_s``, and keep the *last* frame
    whose pixel spread (``std``) clears ``min_std``. Overridable via
    ``MEENOW_CAMERA_FOURCC`` (default ``MJPG``) and ``MEENOW_CAMERA_RESOLUTION``
    (default ``1920x1080``).
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
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 1.0)  # decode YUYV/MJPG to BGR, not raw
        fourcc = os.environ.get("MEENOW_CAMERA_FOURCC", "MJPG").strip()
        if fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc[:4]))
        res = os.environ.get("MEENOW_CAMERA_RESOLUTION", "1920x1080").strip().lower()
        if "x" in res:
            try:
                w, h = (int(v) for v in res.split("x", 1))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            except ValueError:
                log.warning("Ignoring invalid MEENOW_CAMERA_RESOLUTION=%r", res)

        best = None
        best_std = -1.0
        last_good = None
        start = time.monotonic()
        deadline = start + max(0.2, settle_s)
        while time.monotonic() < deadline:
            ok, raw = cap.read()
            frame = _to_bgr(cv2, raw) if ok else None
            if frame is not None:
                std = float(frame.std())
                if std > best_std:
                    best, best_std = frame, std
                if time.monotonic() - start >= skip_s and std >= min_std:
                    last_good = frame
            time.sleep(0.03)

        frame = last_good if last_good is not None else best
        if frame is None:
            return None
        if last_good is None:
            log.warning("No settled frame (best std=%.1f); using best available.", best_std)
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


def _rounded_rect_mask(cv2, shape: tuple[int, int], x: int, y: int,
                       w: int, h: int, r: int) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    r = max(0, min(r, w // 2, h // 2))
    cv2.rectangle(mask, (x + r, y), (x + w - r, y + h), 255, -1)
    cv2.rectangle(mask, (x, y + r), (x + w, y + h - r), 255, -1)
    for cx, cy in ((x + r, y + r), (x + w - r, y + r), (x + r, y + h - r), (x + w - r, y + h - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1)
    return mask


def stitch_photos(back: np.ndarray, front: np.ndarray, *, flip_front: bool = False) -> np.ndarray:
    """Composite the selfie as a rounded inset onto the surroundings shot.

    Ports meenow's ``stitchPhotos`` canvas geometry (``src/screens/capture.ts``):
    inset 35% of the back frame's width in the top-left corner, 3% padding, 8%
    corner radius, 5px white border. ``flip_front`` un-mirrors a photo taken via
    a physical mirror so it reads like a normal selfie.
    """
    import cv2

    if flip_front:
        front = front[:, ::-1]
    h, w = back.shape[:2]
    inset_w = round(w * 0.35)
    inset_h = round(inset_w * front.shape[0] / front.shape[1])
    pad = round(w * 0.03)
    r = round(inset_w * 0.08)
    border = 5

    out = back.copy()
    inset = cv2.resize(front, (inset_w, inset_h))

    outer = _rounded_rect_mask(
        cv2, (h, w), pad - border, pad - border,
        inset_w + 2 * border, inset_h + 2 * border, r + border,
    )
    out[outer > 0] = 255

    inner = _rounded_rect_mask(cv2, (h, w), pad, pad, inset_w, inset_h, r)
    eh = min(inset_h, h - pad)
    ew = min(inset_w, w - pad)
    if eh > 0 and ew > 0:
        roi = out[pad:pad + eh, pad:pad + ew]
        m = inner[pad:pad + eh, pad:pad + ew] > 0
        roi[m] = inset[:eh, :ew][m]
    return out


def zoom(frame: np.ndarray, factor: float) -> np.ndarray:
    """Center-crop digital zoom: keep 1/factor of each dimension, resize back.

    The Reachy Mini camera has no optical zoom, so this is the only way to
    tighten a shot. ``factor <= 1.0`` returns the frame untouched; the resize
    back keeps the stitch geometry (and the composite size) unchanged.
    """
    if factor <= 1.0:
        return frame
    import cv2

    h, w = frame.shape[:2]
    cw, ch = max(2, round(w / factor)), max(2, round(h / factor))
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    return cv2.resize(frame[y0:y0 + ch, x0:x0 + cw], (w, h))


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
