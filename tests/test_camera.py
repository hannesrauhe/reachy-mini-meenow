"""Tests for camera frame selection and the direct-capture fallback."""

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


def test_uses_media_frame_when_present():
    frame = np.ones((4, 4, 3), dtype=np.uint8)
    out = camera.capture_frame(_Robot(frame), allow_synthetic=False)
    assert out.shape == (4, 4, 3)


def test_empty_stream_without_device_or_synthetic_raises():
    with pytest.raises(RuntimeError):
        camera.capture_frame(_Robot(None), allow_synthetic=False)


def test_empty_stream_falls_back_to_synthetic_when_allowed():
    out = camera.capture_frame(_Robot(None), allow_synthetic=True)
    assert out.dtype == np.uint8 and out.ndim == 3 and out.any()


def test_direct_capture_bad_device_returns_none():
    # A nonexistent device must fail gracefully, not raise.
    assert camera.capture_from_device("/dev/does-not-exist", warmup=1) is None


def test_empty_stream_tries_device_then_synthetic(monkeypatch):
    sentinel = np.full((2, 2, 3), 7, dtype=np.uint8)
    monkeypatch.setattr(camera, "capture_from_device", lambda dev, **k: sentinel)
    out = camera.capture_frame(_Robot(None), allow_synthetic=False, device="/dev/video9")
    assert np.array_equal(out, sentinel)
