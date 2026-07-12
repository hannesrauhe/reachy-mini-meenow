"""Tests for camera frame selection, device detection, and direct capture."""

import numpy as np
import pytest

from reachy_mini_meenow import camera


class _Media:
    def __init__(self, frame):
        self._frame = frame

    def get_frame(self):
        return self._frame


class _Robot:
    def __init__(self, frame):
        self.media = _Media(frame)


def test_parse_device_numeric_vs_path():
    assert camera._parse_device("0") == 0
    assert camera._parse_device("2") == 2
    assert camera._parse_device("/dev/video0") == "/dev/video0"


def test_detect_reachy_camera_matches_by_name(monkeypatch):
    listing = {
        "/dev/video0": "Reachy Mini Camera: Reachy Mini",
        "/dev/video1": "Reachy Mini Camera: Reachy Mini",
        "/dev/video10": "bcm2835-codec-decode",
        "/dev/video13": "bcm2835-isp",
    }
    monkeypatch.setattr(camera, "_video_device_names", lambda: listing)
    assert camera.detect_reachy_camera() == ["/dev/video0", "/dev/video1"]


def test_resolve_devices_explicit_overrides_autodetect(monkeypatch):
    monkeypatch.setattr(camera, "detect_reachy_camera", lambda: ["/dev/video0"])
    assert camera.resolve_devices("/dev/video7", auto=True) == ["/dev/video7"]
    assert camera.resolve_devices(None, auto=True) == ["/dev/video0"]
    assert camera.resolve_devices(None, auto=False) == []


def test_device_capture_preferred_over_media(monkeypatch):
    dev_frame = np.full((2, 2, 3), 7, dtype=np.uint8)
    media_frame = np.ones((4, 4, 3), dtype=np.uint8)
    monkeypatch.setattr(camera, "capture_from_device", lambda dev, **k: dev_frame)
    out = camera.capture_frame(_Robot(media_frame), allow_synthetic=False, devices=["/dev/video0"])
    assert np.array_equal(out, dev_frame)


def test_falls_back_to_media_when_no_device(monkeypatch):
    media_frame = np.ones((4, 4, 3), dtype=np.uint8)
    out = camera.capture_frame(_Robot(media_frame), allow_synthetic=False, devices=[])
    assert out.shape == (4, 4, 3)


def test_device_failure_then_media(monkeypatch):
    monkeypatch.setattr(camera, "capture_from_device", lambda dev, **k: None)
    media_frame = np.ones((4, 4, 3), dtype=np.uint8)
    out = camera.capture_frame(_Robot(media_frame), allow_synthetic=False, devices=["/dev/videoX"])
    assert out.shape == (4, 4, 3)


def test_nothing_available_raises():
    with pytest.raises(RuntimeError):
        camera.capture_frame(_Robot(None), allow_synthetic=False, devices=[])


def test_synthetic_when_allowed():
    out = camera.capture_frame(_Robot(None), allow_synthetic=True, devices=[])
    assert out.dtype == np.uint8 and out.ndim == 3 and out.any()


def test_direct_capture_bad_device_returns_none():
    assert camera.capture_from_device("/dev/does-not-exist", settle_s=0.1) is None


def test_to_bgr_normalises_raw_frames():
    import cv2

    gray2d = np.full((4, 4), 100, dtype=np.uint8)
    out = camera._to_bgr(cv2, gray2d)
    assert out.shape == (4, 4, 3)

    gray1c = np.full((4, 4, 1), 100, dtype=np.uint8)
    assert camera._to_bgr(cv2, gray1c).shape == (4, 4, 3)

    bgr = np.zeros((4, 4, 3), dtype=np.uint8)
    assert camera._to_bgr(cv2, bgr) is bgr  # already BGR, passthrough

    yuyv = np.full((4, 4, 2), 128, dtype=np.uint8)  # packed YUYV -> converted
    assert camera._to_bgr(cv2, yuyv).shape[2] == 3

    assert camera._to_bgr(cv2, None) is None
