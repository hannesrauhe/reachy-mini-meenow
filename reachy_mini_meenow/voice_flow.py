"""Left-antenna push-to-talk: hold it down to record a spoken flow command.

The **left** antenna is a soft button (torque released, like the gestures):
push it down and the robot starts recording; release it and the recording is
transcribed, matched to a smart-home flow by the LLM, executed, and answered
with a short spoken reply.

Like :class:`~reachy_mini_meenow.clock_antenna.ClockAntenna` this is a plain
state machine driven by ``update()`` from the app's poll loop — no thread, so
it can never race a capture — and every robot touch is an injectable seam.

Coexistence with the other antenna interactions:

- **Teach (both down)**: the trigger requires the *right* antenna near its up
  pose, so pushing both down stays a teach gesture, never a voice command.
- **Clock (right antenna)**: winding requires the left antenna up, so holding
  the button down naturally blocks it; a countdown in flight also blocks the
  voice trigger via the ``busy`` seam.
- **One-antenna capture tap**: the caller suppresses it while voice is on —
  the left push belongs to voice.

The recording itself runs blocking in the poll loop (the capture path does the
same); :meth:`released` is the stop predicate handed to ``audio.record``.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from . import gestures

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from .flows import FlowClient
    from .llm import FlowPicker
    from .stt import Transcriber
    from .tts import Speaker

log = logging.getLogger(__name__)

IDLE = "idle"
RECORDING = "recording"

_DEBOUNCE = 2  # consecutive matching polls before arming / releasing


class VoiceFlow:
    """Left-antenna push-button state machine (see module doc)."""

    def __init__(
        self,
        reachy_mini,
        *,
        threshold_deg: float = 20.0,
        right_up_tol_deg: float = 25.0,
        cooldown_s: float = 2.0,
        read: Callable | None = None,
        busy: Callable[[], bool] = lambda: False,
        now: Callable[[], float] = time.monotonic,
    ):
        self._robot = reachy_mini
        self._threshold = math.radians(threshold_deg)
        self._right_up_tol = math.radians(right_up_tol_deg)
        self._right_up_rad = gestures.UP_ANTENNAS_RAD[0]
        self._baseline_left = gestures.UP_ANTENNAS_RAD[1]
        self._cooldown_s = cooldown_s
        self._read = read or gestures.read_antennas
        self._busy = busy
        self._now = now
        self.state = IDLE
        self._ready_at = 0.0
        self._pending = 0  # consecutive polls matching the pending edge
        self._release_count = 0
        # Voice-turn collaborators, wired by the app after construction (the
        # state machine itself stays hardware- and network-free):
        self.transcriber: Transcriber | None = None
        self.flows: FlowClient | None = None
        self.picker: FlowPicker | None = None
        self.speaker: Speaker | None = None
        self.max_record_s: float = 15.0
        self.record_device: int | None = None

    def update(self) -> str | None:
        """Advance one poll tick; return ``"start"`` when recording should begin."""
        if self.state == RECORDING or self._now() < self._ready_at:
            return None
        pos = self._read(self._robot)
        if pos is None or len(pos) != 2:
            return None
        right, left = float(pos[0]), float(pos[1])
        pushed = abs(left - self._baseline_left) > self._threshold
        right_up = abs(right - self._right_up_rad) <= self._right_up_tol
        if pushed and right_up and not self._busy():
            self._pending += 1
        else:
            self._pending = 0
        if self._pending >= _DEBOUNCE:
            self.state = RECORDING
            self._pending = 0
            self._release_count = 0
            log.info("Left antenna pushed down — recording.")
            return "start"
        return None

    def released(self) -> bool:
        """Stop predicate for ``audio.record``: left antenna back near up.

        Debounced the same way as the trigger, so a single noisy sample while
        the antenna is still held does not cut the recording short.
        """
        pos = self._read(self._robot)
        if pos is None or len(pos) != 2:
            return False
        near_up = abs(float(pos[1]) - self._baseline_left) <= self._threshold
        self._release_count = self._release_count + 1 if near_up else 0
        return self._release_count >= _DEBOUNCE

    def end_turn(self) -> None:
        """Finish the turn: idle again, with a short cooldown before re-arming."""
        self.state = IDLE
        self._pending = 0
        self._release_count = 0
        self._ready_at = self._now() + self._cooldown_s


def run_turn(wav: bytes, *, transcriber, flows, picker, speaker) -> None:
    """One voice command, start to finish: STT -> pick -> execute -> speak.

    Every stage degrades to a spoken/logged failure rather than raising, so a
    broken flow server or LLM can never kill the poll loop.
    """
    try:
        transcript = transcriber.transcribe(wav)
    except Exception as exc:  # noqa: BLE001
        log.error("Transcription failed: %s", exc)
        speaker.speak("Ich konnte dich nicht verstehen.")
        return
    transcript = (transcript or "").strip()
    log.info("Transcript: %r", transcript)
    if not transcript:
        speaker.speak("Ich habe nichts verstanden.")
        return

    try:
        catalogue = flows.list_flows()
    except Exception as exc:  # noqa: BLE001
        log.error("ListFlows failed: %s", exc)
        speaker.speak("Der Flow-Server ist nicht erreichbar.")
        return

    name, reply_ok, reply_error = picker.pick(transcript, catalogue)
    log.info("LLM picked flow=%r reply_ok=%r reply_error=%r", name, reply_ok, reply_error)
    if name is not None:
        # The flow has already run by the time we speak — pick the reply that
        # matches what actually happened, not what the LLM hoped.
        if flows.run_flow(name):
            speaker.speak(reply_ok or "Erledigt.")
        else:
            speaker.speak(reply_error or "Der Flow konnte nicht gestartet werden.")
    else:
        speaker.speak(reply_ok or "Dafür kenne ich keinen Flow.")
