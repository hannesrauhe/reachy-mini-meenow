"""Request-shape tests for the Pixelfed client (no real network)."""

import json

from reachy_mini_meenow.pixelfed import PixelfedClient, build_status_text


class _FakeResponse:
    def __init__(self, *, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    """Stands in for an httpx.Client: records get/post calls, returns queued responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._responses.pop(0)


def test_build_status_text():
    assert build_status_text(None) == "#meenowApp"
    assert build_status_text("") == "#meenowApp"
    assert build_status_text("hi") == "hi\n\n#meenowApp"


def test_upload_media_multipart_and_ready_url():
    client = _FakeClient([_FakeResponse(payload={"id": "42", "url": "https://x/y.jpg"})])
    px = PixelfedClient("pixelfed.social", "tok", client=client)
    media_id = px.upload_media(b"jpegbytes", "alt text")
    assert media_id == "42"
    method, url, kwargs = client.calls[0]
    assert method == "POST"
    assert url == "https://pixelfed.social/api/v1/media"
    # multipart file with the meenow.jpg filename + description field
    assert kwargs["files"]["file"][0] == "meenow.jpg"
    assert kwargs["files"]["file"][2] == "image/jpeg"
    assert kwargs["data"]["description"] == "alt text"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


def test_upload_media_polls_until_url_ready():
    client = _FakeClient([
        _FakeResponse(payload={"id": "7", "url": None}),   # initial: still processing
        _FakeResponse(payload={"url": None}),               # poll 1
        _FakeResponse(payload={"url": "https://x/7.jpg"}),  # poll 2: ready
    ])
    px = PixelfedClient("pixelfed.social", "tok", client=client)
    import reachy_mini_meenow.pixelfed as pf
    orig = pf.time.sleep
    pf.time.sleep = lambda *_: None
    try:
        assert px.upload_media(b"x", "alt") == "7"
    finally:
        pf.time.sleep = orig
    assert [c[0] for c in client.calls] == ["POST", "GET", "GET"]


def test_upload_media_raises_on_error_status():
    client = _FakeClient([_FakeResponse(status_code=400)])
    px = PixelfedClient("pixelfed.social", "tok", client=client)
    try:
        px.upload_media(b"x", "alt")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "400" in str(exc)


def test_post_status_body_is_private_with_tag():
    client = _FakeClient([_FakeResponse(payload={"url": "https://pixelfed.social/p/1"})])
    px = PixelfedClient("pixelfed.social", "tok", client=client)
    url = px.post_status(["42"], "caption here")
    assert url == "https://pixelfed.social/p/1"
    method, endpoint, kwargs = client.calls[0]
    assert method == "POST"
    assert endpoint == "https://pixelfed.social/api/v1/statuses"
    body = kwargs["json"]
    assert body == {
        "status": "caption here\n\n#meenowApp",
        "media_ids": ["42"],
        "visibility": "private",
    }
    # sanity: body is JSON-serialisable
    json.dumps(body)
