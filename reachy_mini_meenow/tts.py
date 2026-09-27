"""Text-to-speech for the robot's spoken reply (local piper, German voice).

Two backends behind the same ``text -> WAV bytes`` seam:

* :func:`load_piper_voice` — the resident Python API (``PiperVoice``): the
  ONNX model is loaded once and kept warm, so each reply synthesises in
  seconds. Strongly preferred on slow machines (a Raspberry Pi reloads a
  60 MB model per CLI invocation, which costs ~20 s per reply).
* :func:`synthesize_piper` — the piper CLI as a one-shot subprocess. Used as
  a fallback when the Python API is unavailable; pays the model load per call.

The provider is a plain callable seam so a cloud TTS (e.g. Mistral) can be
added later without touching the turn orchestrator.
"""

from __future__ import annotations

import io
import logging
import subprocess
import tempfile
import wave

log = logging.getLogger(__name__)

_TIMEOUT_S = 30.0


def load_piper_voice(voice: str):
    """Load a piper voice once and return a resident ``synth(text) -> wav``.

    Raises on import/load failure so the caller can fall back to the CLI.
    """
    from piper import PiperVoice  # lazy: only needed when a voice is configured

    log.info("Loading piper voice %s ...", voice)
    piper_voice = PiperVoice.load(voice)
    log.info("Piper voice loaded.")

    def synth(text: str) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            piper_voice.synthesize_wav(text, w)
        return buf.getvalue()

    return synth


def synthesize_piper(text: str, *, bin_path: str, voice: str,
                     run=subprocess.run) -> bytes:
    """Synthesize ``text`` with the piper CLI; return WAV bytes."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        res = run(
            [bin_path, "--model", voice, "--output_file", path],
            input=text.encode("utf-8"), capture_output=True, timeout=_TIMEOUT_S,
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"piper failed ({res.returncode}): "
                f"{res.stderr.decode(errors='replace')[-400:]}"
            )
        with open(path, "rb") as f:
            return f.read()
    finally:
        import os

        os.unlink(path)


class Speaker:
    """Speak a sentence: synthesize (if a voice is configured) and play.

    ``synth`` maps text -> WAV bytes; ``play`` maps WAV bytes -> None. Both
    are injectable, so tests never touch audio hardware and a future cloud
    TTS is just a different ``synth``.
    """

    def __init__(self, *, synth=None, play=None, beep=None):
        self._synth = synth
        self._play = play
        self._beep = beep

    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        log.info("Speaking: %s", text)
        try:
            if self._synth is not None and self._play is not None:
                self._play(self._synth(text))
            elif self._beep is not None:
                self._beep()  # no voice configured: at least acknowledge
        except Exception as exc:  # noqa: BLE001 - speech is best-effort
            log.warning("speak failed: %s", exc)

    def acknowledge(self) -> None:
        """Earcon for recording start/stop (no-op when audio is unavailable)."""
        try:
            if self._beep is not None:
                self._beep()
        except Exception as exc:  # noqa: BLE001
            log.warning("beep failed: %s", exc)
