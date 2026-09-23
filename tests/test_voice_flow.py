"""State-machine tests for the left-antenna push-to-talk button (no hardware)."""

from reachy_mini_meenow.voice_flow import IDLE, RECORDING, VoiceFlow, run_turn

UP_R, UP_L = -0.1745, 0.1745  # SDK up pose: [right, left]


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make(read, busy=lambda: False, cooldown_s=0.0):
    now = FakeClock()
    v = VoiceFlow(
        None, threshold_deg=20.0, cooldown_s=cooldown_s,
        read=lambda _robot: read(), busy=busy, now=now,
    )
    return v, now


def test_start_needs_two_debounced_polls_of_left_down_right_up():
    state = {"left": UP_L}
    v, _ = make(lambda: [UP_R, state["left"]])
    state["left"] = UP_L + 0.6  # pushed down
    assert v.update() is None       # 1st matching poll: pending
    assert v.update() == "start"    # 2nd: debounced, recording
    assert v.state == RECORDING


def test_both_down_is_teach_not_voice():
    # Right antenna also deflected -> the teach gesture owns it, voice stays idle.
    v, _ = make(lambda: [UP_R + 0.6, UP_L + 0.6])
    for _ in range(5):
        assert v.update() is None
    assert v.state == IDLE


def test_busy_blocks_voice():
    v, _ = make(lambda: [UP_R, UP_L + 0.6], busy=lambda: True)
    for _ in range(5):
        assert v.update() is None
    assert v.state == IDLE


def test_released_debounce_and_end_turn():
    state = {"left": UP_L + 0.6}
    v, _ = make(lambda: [UP_R, state["left"]])
    assert v.update() is None
    assert v.update() == "start"
    assert v.released() is False  # still held down
    state["left"] = UP_L          # released
    assert v.released() is False  # 1st near-up poll: pending
    assert v.released() is True   # 2nd: stop the recording
    v.end_turn()
    assert v.state == IDLE


def test_cooldown_delays_rearm():
    v, now = make(lambda: [UP_R, UP_L + 0.6], cooldown_s=5.0)
    assert v.update() is None
    assert v.update() == "start"
    v.end_turn()
    now.advance(1.0)
    assert v.update() is None   # still in cooldown
    now.advance(5.0)
    assert v.update() is None   # cooldown over, first matching poll
    assert v.update() == "start"


def test_no_reading_means_no_trigger():
    v, _ = make(lambda: None)
    for _ in range(5):
        assert v.update() is None


class _FakeTranscriber:
    def __init__(self, text=None, exc=None):
        self.text, self.exc = text, exc

    def transcribe(self, wav):
        if self.exc:
            raise self.exc
        return self.text


class _FakeFlows:
    def __init__(self, catalogue=None, list_exc=None, run_ok=True):
        self.catalogue = catalogue or {"SalonAn": {"Description": "Salon an"}}
        self.list_exc = list_exc
        self.run_ok = run_ok
        self.runs = []

    def list_flows(self):
        if self.list_exc:
            raise self.list_exc
        return self.catalogue

    def run_flow(self, name):
        self.runs.append(name)
        return self.run_ok


class _FakePicker:
    def __init__(self, result):
        self.result = result

    def pick(self, transcript, flows):
        return self.result


class _FakeSpeaker:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


def test_run_turn_happy_path():
    flows = _FakeFlows()
    speaker = _FakeSpeaker()
    run_turn(
        b"wav",
        transcriber=_FakeTranscriber("mach den salon an"),
        flows=flows,
        picker=_FakePicker(("SalonAn", "Salon wird angemacht.", "Das hat nicht geklappt.")),
        speaker=speaker,
    )
    assert flows.runs == ["SalonAn"]
    assert speaker.spoken == ["Salon wird angemacht."]


def test_run_turn_no_flow_match():
    flows = _FakeFlows()
    speaker = _FakeSpeaker()
    run_turn(
        b"wav",
        transcriber=_FakeTranscriber("quatsch"),
        flows=flows,
        picker=_FakePicker((None, "Da kenne ich keinen Flow.", "")),
        speaker=speaker,
    )
    assert flows.runs == []
    assert speaker.spoken == ["Da kenne ich keinen Flow."]


def test_run_turn_stt_failure_speaks_apology():
    speaker = _FakeSpeaker()
    run_turn(
        b"wav",
        transcriber=_FakeTranscriber(exc=RuntimeError("boom")),
        flows=_FakeFlows(),
        picker=_FakePicker(("SalonAn", "x", "")),
        speaker=speaker,
    )
    assert speaker.spoken  # something was said, nothing raised


def test_run_turn_empty_transcript():
    speaker = _FakeSpeaker()
    run_turn(
        b"wav",
        transcriber=_FakeTranscriber("   "),
        flows=_FakeFlows(),
        picker=_FakePicker(("SalonAn", "x", "")),
        speaker=speaker,
    )
    assert speaker.spoken and "nichts" in speaker.spoken[0].lower()


def test_run_turn_flow_execution_failure():
    # the flow already ran (and failed) before speaking -> the LLM's error
    # variant is what gets spoken, not the success confirmation
    speaker = _FakeSpeaker()
    run_turn(
        b"wav",
        transcriber=_FakeTranscriber("salon an"),
        flows=_FakeFlows(run_ok=False),
        picker=_FakePicker(("SalonAn", "Salon an.", "Das hat mit dem Flow nicht geklappt.")),
        speaker=speaker,
    )
    assert speaker.spoken == ["Das hat mit dem Flow nicht geklappt."]


def test_run_turn_execution_failure_without_error_reply():
    # LLM gave no reply_error -> generic fallback is spoken
    speaker = _FakeSpeaker()
    run_turn(
        b"wav",
        transcriber=_FakeTranscriber("salon an"),
        flows=_FakeFlows(run_ok=False),
        picker=_FakePicker(("SalonAn", "Salon an.", "")),
        speaker=speaker,
    )
    assert speaker.spoken and "nicht" in speaker.spoken[0]
