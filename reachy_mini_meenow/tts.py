"""Text-to-speech for the robot's spoken reply (local piper, German voice).

``piper`` is a CLI: ``piper --model <voice.onnx> --output_file <out.wav>``
reads the text on stdin and writes a PCM WAV, which ``audio.play`` plays on
the robot's speaker. A subprocess per reply is fine — piper synthesises a
sentence in well under a second and the voice turn is already blocking.

The provider is a plain callable seam so a cloud TTS (e.g. Mistral) can be
added later without touching the turn orchestrator.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile

log = logging.getLogger(__name__)

_TIMEOUT_S = 30.0


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
            log.debug("beep failed: %s", exc)
