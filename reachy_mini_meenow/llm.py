"""Pick a smart-home flow for a spoken command with an OpenAI-compatible LLM.

One ``POST {base_url}/chat/completions`` call with a strict JSON reply:

``{"flow": "<exact flow name or null>", "reply": "<short German sentence>"}``

The returned flow name is validated against the catalogue the model was given:
a hallucinated name never reaches :meth:`flows.FlowClient.run_flow`.
"""

from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger(__name__)

_TIMEOUT_S = 30.0

_SYSTEM = (
    "Du bist die Sprachsteuerung eines Smart-Home-Roboters.\n"
    "Der Nutzer hat ein Kommando gesprochen (transkribiert, kann Fehler enthalten).\n"
    "Wähle GENAU EINEN Flow aus der angegebenen Liste, der das Kommando ausführt.\n"
    "Regeln:\n"
    "- Antworte ausschließlich mit einem JSON-Objekt ohne Markdown: "
    '{"flow": "<Name>", "reply_ok": "<kurz>", "reply_error": "<kurz>"}\n'
    '- "flow" ist der exakte Key aus der Liste. Passt kein Flow, dann null.\n'
    "- Flows mit Kind 'helper' sind interne Bausteine und werden NIE gewählt.\n"
    "- Cron-/Event-Flows (minutely, hourly, mqtt, alert, influx, cron) sind "
    "Hintergrundautomatisierungen und nur wählen, wenn der Nutzer sie explizit meint.\n"
    '- "reply_ok" ist ein kurzer deutscher Satz (max 10 Wörter) als Bestätigung, '
    "dass der Flow ausgeführt wurde — bzw. eine knappe Absage, wenn kein Flow "
    "passt. Passt kein Flow, ist reply_error irrelevant (leerer String).\n"
    '- "reply_error" ist ein kurzer deutscher Satz (max 10 Wörter) für den Fall, '
    "dass der Flow fehlgeschlagen ist (z.B. 'Das hat mit dem Flow nicht "
    "geklappt.').\n"
    "- Keine Anführungszeichen, keine Emojis.\n- Verstanden?"
)


def _catalogue(flows: dict) -> str:
    """Compact ``name | kind | description`` lines for the prompt."""
    lines = []
    for name, meta in sorted(flows.items()):
        meta = meta if isinstance(meta, dict) else {}
        kind = meta.get("Kind") or "?"
        desc = " ".join(str(meta.get("Description") or "").split())
        lines.append(f"{name} | {kind} | {desc}")
    return "\n".join(lines)


def _extract_json(content: str) -> dict | None:
    """Parse the model's JSON, tolerating code fences and surrounding prose."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class FlowPicker:
    """Ask an OpenAI-compatible endpoint which flow the command means."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=_TIMEOUT_S, trust_env=True)

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def pick(self, transcript: str, flows: dict) -> tuple[str | None, str, str]:
        """Return ``(flow_name_or_None, reply_ok, reply_error)`` for the transcript.

        ``reply_ok`` is what to speak when the flow ran (or when no flow fits —
        then it is the refusal); ``reply_error`` is what to speak when the flow
        call itself failed. The caller picks based on the HTTP status, because
        only the robot knows whether the execution actually succeeded.

        Any transport/parse failure yields ``(None, apology, "")`` so the
        caller can always speak something rather than dying silently.
        """
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": f"Verfügbare Flows (name | kind | beschreibung):\n"
                    f"{_catalogue(flows)}\n\nGesprochenes Kommando: {transcript}",
                },
            ],
        }
        try:
            res = self.client.post(
                f"{self.base_url}/chat/completions", headers=self._headers, json=body
            )
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            log.warning("LLM request failed: %s", exc)
            return None, "Ich habe die Verbindung nicht hingekriegt.", ""

        data = _extract_json(content)
        if data is None:
            log.warning("LLM reply was not JSON: %r", content)
            return None, "Ich habe dich nicht verstanden.", ""

        name = data.get("flow")
        reply_ok = str(data.get("reply_ok") or data.get("reply") or "").strip()
        reply_error = str(data.get("reply_error") or "").strip()
        if name is not None and name not in flows:
            log.warning("LLM picked unknown flow %r — ignoring.", name)
            return None, reply_ok or "Dafür kenne ich keinen Flow.", ""
        return (name if isinstance(name, str) else None), reply_ok, reply_error
