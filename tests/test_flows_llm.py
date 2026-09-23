"""Request-shape tests for the flow client and LLM picker (no real network)."""

import httpx

from reachy_mini_meenow.flows import FlowClient
from reachy_mini_meenow.llm import FlowPicker, _extract_json


class _FakeResponse:
    def __init__(self, *, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://x")
            raise httpx.HTTPStatusError(
                "boom", request=request,
                response=httpx.Response(self.status_code, request=request),
            )


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


# --- FlowClient -------------------------------------------------------------

def test_list_flows_url_and_shape():
    catalogue = {"SalonAn": {"DisplayName": "SalonAn", "Kind": "event"}}
    client = _FakeClient([_FakeResponse(payload=catalogue)])
    fc = FlowClient("http://raspi:8080", client=client)
    assert fc.list_flows() == catalogue
    assert client.calls[0] == ("GET", "http://raspi:8080/flowbuilder/ListFlows", {})


def test_run_flow_posts_and_quotes():
    client = _FakeClient([_FakeResponse(status_code=200)])
    fc = FlowClient("http://raspi:8080/", client=client)
    assert fc.run_flow("big button") is True
    method, url, _ = client.calls[0]
    assert method == "POST"
    assert url == "http://raspi:8080/flow/big%20button"


def test_run_flow_reports_failure():
    client = _FakeClient([_FakeResponse(status_code=500)])
    assert FlowClient("http://raspi", client=client).run_flow("x") is False


# --- FlowPicker -------------------------------------------------------------

def _llm_response(content):
    return _FakeResponse(payload={"choices": [{"message": {"content": content}}]})


FLOWS = {
    "SalonAn": {"Description": "Salon socket on", "Kind": "event"},
    "WZaus": {"Description": "Everything off", "Kind": "manual"},
}


def test_pick_returns_flow_and_replies():
    client = _FakeClient([_llm_response(
        '{"flow": "SalonAn", "reply_ok": "Salon an.", "reply_error": "Hat nicht geklappt."}'
    )])
    picker = FlowPicker("http://llm:11434/v1", "m", api_key="k", client=client)
    assert picker.pick("salon an", FLOWS) == (
        "SalonAn", "Salon an.", "Hat nicht geklappt.",
    )
    method, url, kwargs = client.calls[0]
    assert method == "POST"
    assert url == "http://llm:11434/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer k"
    assert kwargs["json"]["model"] == "m"
    assert kwargs["json"]["temperature"] == 0
    # the catalogue and the transcript both reach the prompt
    prompt = kwargs["json"]["messages"][1]["content"]
    assert "SalonAn" in prompt and "salon an" in prompt


def test_pick_rejects_unknown_flow():
    client = _FakeClient([_llm_response(
        '{"flow": "ErfindeMich", "reply_ok": "ok", "reply_error": ""}'
    )])
    picker = FlowPicker("http://llm/v1", "m", client=client)
    name, _ok, _err = picker.pick("irgendwas", FLOWS)
    assert name is None


def test_pick_null_flow_passes_through():
    client = _FakeClient([_llm_response(
        '{"flow": null, "reply_ok": "Kein Flow.", "reply_error": ""}'
    )])
    picker = FlowPicker("http://llm/v1", "m", client=client)
    assert picker.pick("quatsch", FLOWS) == (None, "Kein Flow.", "")


def test_pick_accepts_legacy_reply_key():
    # older prompts answer with a single "reply" — still usable as reply_ok
    client = _FakeClient([_llm_response('{"flow": "WZaus", "reply": "Aus."}')])
    picker = FlowPicker("http://llm/v1", "m", client=client)
    assert picker.pick("alles aus", FLOWS) == ("WZaus", "Aus.", "")


def test_pick_non_json_reply_degrades():
    client = _FakeClient([_llm_response("Ich weiß nicht.")])
    picker = FlowPicker("http://llm/v1", "m", client=client)
    name, ok, err = picker.pick("hallo", FLOWS)
    assert name is None and ok and err == ""


def test_pick_http_error_degrades():
    client = _FakeClient([_FakeResponse(status_code=500)])
    picker = FlowPicker("http://llm/v1", "m", client=client)
    name, ok, err = picker.pick("hallo", FLOWS)
    assert name is None and ok and err == ""


def test_extract_json_tolerates_code_fences():
    assert _extract_json('```json\n{"flow": "A", "reply": "x"}\n```') == {
        "flow": "A", "reply": "x"
    }
    assert _extract_json('Hier: {"flow": null} danke') == {"flow": None}
    assert _extract_json("kein json") is None
