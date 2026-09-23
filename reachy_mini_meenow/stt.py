"""Speech-to-text for the recorded command (always German).

Providers:

- ``whisper_cpp`` (default): a resident **whisper-server** (whisper.cpp) —
  ``POST /inference`` multipart, model loaded once so a command transcribes in
  a fraction of a second. The app spawns the server when it is not already up
  (see :func:`ensure_server` / :func:`stop_server`).
- ``whisper_cli``: one-shot ``whisper-cli`` subprocess per command — no daemon,
  but pays the model reload every time. Good fallback / smoke test.
- ``mistral``: ``POST /v1/audio/transcriptions`` (Voxtral) with an API key.

All take 16-bit PCM WAV bytes (exactly what ``audio.record`` produces).
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time

import httpx

log = logging.getLogger(__name__)

_TIMEOUT_S = 60.0
_WAV = "audio/wav"


def transcribe_server(wav: bytes, url: str, lang: str,
                      client: httpx.Client | None = None) -> str:
    """Transcribe via whisper-server's OpenAI-ish ``/inference`` endpoint."""
    client = client or httpx.Client(timeout=_TIMEOUT_S, trust_env=True)
    res = client.post(
        f"{url.rstrip('/')}/inference",
        files={"file": ("command.wav", wav, _WAV)},
        data={"language": lang, "response_format": "json",
              "no_timestamps": "true", "model": "whisper-1"},
    )
    res.raise_for_status()
    data = res.json()
    # whisper-server's json format answers with "text"; "transcription" is the
    # OpenAI-compatible spelling some builds use — accept either.
    return str(data.get("text") or data.get("transcription") or "").strip()


def transcribe_cli(wav: bytes, *, bin_path: str, model: str, lang: str,
                   threads: int, run=subprocess.run) -> str:
    """Transcribe with a one-shot whisper-cli subprocess (stdout with -np)."""
    # whisper-cli loads audio from a real file (miniaudio, no stdin) and -np
    # makes stdout *only* the transcript — v1.9.4 has no --output-format flag.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav)
        path = f.name
    try:
        res = run(
            [bin_path, "-m", model, "-f", path, "-l", lang, "-np",
             "-t", str(threads)],
            capture_output=True, timeout=_TIMEOUT_S,
        )
    finally:
        os.unlink(path)
    if res.returncode != 0:
        raise RuntimeError(
            f"whisper-cli failed ({res.returncode}): "
            f"{res.stderr.decode(errors='replace')[-400:]}"
        )
    return res.stdout.decode(errors="replace").strip()


def transcribe_mistral(wav: bytes, *, api_key: str, model: str,
                       base_url: str = "https://api.mistral.ai/v1",
                       client: httpx.Client | None = None) -> str:
    """Transcribe via the Mistral (Voxtral) transcription endpoint."""
    client = client or httpx.Client(timeout=_TIMEOUT_S, trust_env=True)
    res = client.post(
        f"{base_url.rstrip('/')}/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("command.wav", wav, _WAV)},
        data={"model": model, "language": "de"},
    )
    res.raise_for_status()
    return str(res.json().get("text", "")).strip()


class Transcriber:
    """Pick a provider once, expose ``transcribe(wav) -> str``.

    ``run``/``client`` are injectable for tests; the server URL is probed
    lazily so a manually started whisper-server is simply reused.
    """

    def __init__(self, provider: str, *, server_url: str, cli_bin: str,
                 model: str, lang: str, threads: int,
                 mistral_key: str | None = None,
                 mistral_model: str = "voxtral-mini-2602",
                 client: httpx.Client | None = None,
                 run=subprocess.run):
        self.provider = provider
        self.server_url = server_url
        self.cli_bin = cli_bin
        self.model = model
        self.lang = lang
        self.threads = threads
        self.mistral_key = mistral_key
        self.mistral_model = mistral_model
        self.client = client or httpx.Client(timeout=_TIMEOUT_S, trust_env=True)
        self._run = run

    def transcribe(self, wav: bytes) -> str:
        if self.provider == "whisper_cpp":
            return transcribe_server(wav, self.server_url, self.lang, self.client)
        if self.provider == "whisper_cli":
            return transcribe_cli(wav, bin_path=self.cli_bin, model=self.model,
                                  lang=self.lang, threads=self.threads, run=self._run)
        if self.provider == "mistral":
            if not self.mistral_key:
                raise RuntimeError("MEENOW_MISTRAL_API_KEY not set")
            return transcribe_mistral(wav, api_key=self.mistral_key,
                                      model=self.mistral_model, client=self.client)
        raise RuntimeError(f"unknown STT provider {self.provider!r}")


def server_healthy(url: str, client: httpx.Client | None = None) -> bool:
    """True when a whisper-server at ``url`` answers /health."""
    client = client or httpx.Client(timeout=3.0, trust_env=True)
    try:
        return client.get(f"{url.rstrip('/')}/health").status_code == 200
    except httpx.HTTPError:
        return False


def ensure_server(url: str, *, server_bin: str, model: str, threads: int,
                  popen=subprocess.Popen, sleep=time.sleep,
                  wait_s: float = 60.0) -> subprocess.Popen | None:
    """Return a running whisper-server, spawning one if /health is silent.

    ``None`` means an external server was already up (nothing to stop).
    """
    if server_healthy(url):
        log.info("Reusing whisper-server at %s", url)
        return None
    from urllib.parse import urlparse

    port = urlparse(url).port or 80
    log.info("Starting whisper-server (%s, model %s)...", server_bin, model)
    proc = popen(
        [server_bin, "-m", model, "--host", "127.0.0.1", "--port", str(port),
         "-t", str(threads)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"whisper-server exited ({proc.returncode})")
        if server_healthy(url):
            log.info("whisper-server ready at %s", url)
            return proc
        sleep(0.5)
    proc.terminate()
    raise RuntimeError("whisper-server did not become healthy in time")


def stop_server(proc: subprocess.Popen | None) -> None:
    """Terminate a server we spawned (no-op for an externally started one)."""
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001 - shutdown cleanup only
        proc.kill()
