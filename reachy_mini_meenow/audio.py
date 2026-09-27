"""Microphone recording and speaker playback (direct ALSA via sounddevice).

The app runs with the SDK's ``no_media`` backend, which makes the daemon
release the robot's audio hardware — so mic and speaker are free for direct
access here, exactly like the camera is captured directly in ``camera.py``.

Everything is mono 16 kHz int16 — the format whisper.cpp wants and what piper
outputs — with the WAV container built by the stdlib :mod:`wave` module.
``sounddevice`` is imported lazily so the rest of the app (and every unit
test) works on machines without audio hardware.
"""

from __future__ import annotations

import io
import logging
import math
import time
import wave

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000  # what the ReSpeaker array and whisper.cpp both use


def _open_input(device: int | None, block: int, callback):
    import sounddevice as sd  # lazy: hardware-dependent

    return sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16",
        blocksize=block, device=device, callback=callback,
    )


def _open_output(device: int | None):
    import sounddevice as sd

    return sd.OutputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=device
    )


def encode_wav(samples: np.ndarray) -> bytes:
    """int16 mono samples -> 16 kHz mono PCM WAV bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def decode_wav(data: bytes) -> np.ndarray:
    """WAV bytes -> int16 mono samples at 16 kHz (down-mix + linear resample).

    Stdlib-only so piper's output (typically 22050 Hz mono PCM) plays without
    pulling in another audio library.
    """
    with wave.open(io.BytesIO(data), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise RuntimeError(f"only 16-bit WAV supported, got {width * 8}-bit")
    samples = np.frombuffer(raw, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    mono = samples.astype("int16")
    if rate != SAMPLE_RATE:
        n = max(1, int(len(mono) * SAMPLE_RATE / rate))
        x_old = np.linspace(0.0, 1.0, num=len(mono), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
        mono = np.interp(x_new, x_old, mono).astype("int16")
    return mono


def record(max_s: float, stop: callable, *, device: int | None = None,
           poll_s: float = 0.1) -> bytes:
    """Record from the mic until ``stop()`` or ``max_s``; return WAV bytes.

    ``stop`` is polled between audio blocks — the caller wires it to the left
    antenna position, so releasing the antenna ends the recording.
    """
    block = SAMPLE_RATE // 10  # 100 ms blocks
    chunks: list[np.ndarray] = []

    def callback(indata, frames, _t, status):
        if status:
            log.debug("input status: %s", status)
        chunks.append(indata.copy())

    with _open_input(device, block, callback) as stream:
        start = time.monotonic()
        while time.monotonic() - start < max_s:
            if stop():
                break
            time.sleep(poll_s)
        time.sleep(0.15)  # catch the block in flight when stop() fired
    stream.stop()
    audio = (
        np.concatenate([c.reshape(-1) for c in chunks])
        if chunks else np.zeros(0, dtype="int16")
    )
    log.info("Recorded %.1fs of audio.", len(audio) / SAMPLE_RATE)
    return encode_wav(audio)


def play(wav: bytes, *, device: int | None = None) -> None:
    """Play WAV bytes on the speaker (blocking until done)."""
    samples = decode_wav(wav)
    with _open_output(device) as out:
        out.write(samples.reshape(-1, 1))


def beep(*, device: int | None = None, freq: float = 880.0,
         duration_s: float = 0.25, volume: float = 0.7) -> None:
    """Short sine earcon so recording start/stop is audible.

    Padded with silence front and back: opening a fresh ALSA stream drops the
    first ~100 ms while the buffer fills, which would otherwise eat a short
    tone entirely (long speech only loses its inaudible head).
    """
    n = int(SAMPLE_RATE * duration_s)
    t = np.arange(n) / SAMPLE_RATE
    fade = min(n // 8, 64)
    tone = volume * np.sin(2 * math.pi * freq * t) * 32767
    tone[:fade] *= np.linspace(0, 1, fade)
    tone[-fade:] *= np.linspace(1, 0, fade)
    pad = np.zeros(int(SAMPLE_RATE * 0.1), dtype="float64")
    samples = np.concatenate([pad, tone, pad])
    with _open_output(device) as out:
        out.write(samples.astype("int16").reshape(-1, 1))
