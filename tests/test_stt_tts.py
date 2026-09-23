"""Provider tests for STT and TTS (no audio hardware, no network)."""

import pytest

from reachy_mini_meenow import stt, tts
from reachy_mini_meenow.stt import Transcriber


class _FakeResponse:
    def __init__(self, *, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._responses.pop(0)


def _transcriber(provider, client=None, run=None):
    return Transcriber(
        provider,
        server_url="http://w:8180",
        cli_bin="/bin/whisper-cli",
        model="/models/de.bin",
        lang="de",
        threads=4,
        mistral_key="mk",
        client=client,
        run=run,
    )


def test_whisper_server_multipart_and_german():
    client = _FakeClient([_FakeResponse(payload={"text": " Salon an. "})])
    t = _transcriber("whisper_cpp", client=client)
    assert t.transcribe(b"wav") == "Salon an."
    method, url, kwargs = client.calls[0]
    assert (method, url) == ("POST", "http://w:8180/inference")
    assert kwargs["data"]["language"] == "de"
    assert kwargs["data"]["response_format"] == "json"
    assert kwargs["files"]["file"][1] == b"wav"


def test_whisper_cli_argv_and_stdout():
    calls = {}

    class _Res:
        returncode = 0
        stdout = b"  guten morgen  \n"
        stderr = b""

    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        return _Res()

    t = _transcriber("whisper_cli", run=fake_run)
    assert t.transcribe(b"wav") == "guten morgen"
    argv = calls["argv"]
    assert argv[0] == "/bin/whisper-cli"
    assert argv[argv.index("-m") + 1] == "/models/de.bin"
    assert argv[argv.index("-l") + 1] == "de"
    assert "-np" in argv
    assert argv[argv.index("-t") + 1] == "4"


def test_whisper_cli_failure_raises():
    class _Res:
        returncode = 1
        stdout = b""
        stderr = b"boom"

    t = _transcriber("whisper_cli", run=lambda argv, **kw: _Res())
    with pytest.raises(RuntimeError):
        t.transcribe(b"wav")


def test_mistral_transcriptions_call():
    client = _FakeClient([_FakeResponse(payload={"text": "licht an"})])
    t = _transcriber("mistral", client=client)
    assert t.transcribe(b"wav") == "licht an"
    method, url, kwargs = client.calls[0]
    assert (method, url) == ("POST", "https://api.mistral.ai/v1/audio/transcriptions")
    assert kwargs["headers"]["Authorization"] == "Bearer mk"
    assert kwargs["data"]["language"] == "de"


def test_unknown_provider_raises():
    with pytest.raises(RuntimeError):
        _transcriber("wat").transcribe(b"wav")


def test_ensure_server_reuses_healthy(monkeypatch):
    monkeypatch.setattr(stt, "server_healthy", lambda url, client=None: True)
    proc = stt.ensure_server(
        "http://w:8180", server_bin="/bin/ws", model="m", threads=4,
        popen=lambda *a, **k: pytest.fail("must not spawn when healthy"),
    )
    assert proc is None  # external server: nothing for us to stop


def test_ensure_server_spawns_when_down(monkeypatch):
    spawned = {}

    class _Proc:
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            pass

    def fake_popen(argv, **kwargs):
        spawned["argv"] = argv
        return _Proc()

    monkeypatch.setattr(stt, "server_healthy", lambda url, client=None: False)
    # first health probe after spawn succeeds
    probes = iter([False, True])
    monkeypatch.setattr(
        stt, "server_healthy",
        lambda url, client=None: next(probes, True),
    )
    proc = stt.ensure_server(
        "http://127.0.0.1:8180", server_bin="/bin/ws", model="m", threads=4,
        popen=fake_popen, sleep=lambda _s: None,
    )
    assert proc is not None
    argv = spawned["argv"]
    assert argv[0] == "/bin/ws" and argv[argv.index("-m") + 1] == "m"
    assert argv[argv.index("--port") + 1] == "8180"


def test_stop_server_none_is_noop():
    stt.stop_server(None)


# --- TTS --------------------------------------------------------------------

def test_piper_argv_and_wav_output():
    def fake_run(argv, **kwargs):
        # piper writes the WAV to --output_file; fake it here
        out = argv[argv.index("--output_file") + 1]
        with open(out, "wb") as f:
            f.write(b"RIFFfake-wav")

        class _Res:
            returncode = 0
            stderr = b""

        return _Res()

    wav = tts.synthesize_piper("Guten Tag", bin_path="/bin/piper",
                               voice="/voices/de.onnx", run=fake_run)
    assert wav == b"RIFFfake-wav"


def test_piper_failure_raises():
    class _Res:
        returncode = 2
        stderr = b"no voice"

    with pytest.raises(RuntimeError):
        tts.synthesize_piper("x", bin_path="/bin/piper", voice="v",
                             run=lambda argv, **kw: _Res())


def test_speaker_speak_and_degrade():
    played = []
    sp = tts.Speaker(synth=lambda t: b"W" * len(t), play=played.append,
                     beep=lambda: played.append("beep"))
    sp.speak("hi")
    assert played == [b"WW"]
    sp.speak("")            # nothing to say -> silent
    assert played == [b"WW"]
    # no voice configured -> acknowledge with a beep
    played.clear()
    tts.Speaker(synth=None, play=None, beep=lambda: played.append("beep")).acknowledge()
    assert played == ["beep"]


def test_speaker_swallows_synthesis_errors():
    def boom(_text):
        raise RuntimeError("piper died")

    tts.Speaker(synth=boom, play=lambda _w: None).speak("hallo")  # must not raise
