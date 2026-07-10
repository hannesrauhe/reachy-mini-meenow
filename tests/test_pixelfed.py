"""Request-shape tests for the Pixelfed client (no real network)."""

import json

from reachy_mini_meenow.pixelfed import PixelfedClient, build_status_text


class _FakeResponse:
    def __init__(self, *, ok=True, status_code=200, payload=None):
        self.ok = ok
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


class _FakeSession:
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
    session = _FakeSession([_FakeResponse(payload={"id": "42", "url": "https://x/y.jpg"})])
    client = PixelfedClient("pixelfed.social", "tok", session=session)
    media_id = client.upload_media(b"jpegbytes", "alt text")
    assert media_id == "42"
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://pixelfed.social/api/v1/media"
    # multipart file with the meenow.jpg filename + description field
    assert kwargs["files"]["file"][0] == "meenow.jpg"
    assert kwargs["files"]["file"][2] == "image/jpeg"
    assert kwargs["data"]["description"] == "alt text"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


def test_upload_media_polls_until_url_ready():
    session = _FakeSession([
        _FakeResponse(payload={"id": "7", "url": None}),   # initial: still processing
        _FakeResponse(payload={"url": None}),               # poll 1
        _FakeResponse(payload={"url": "https://x/7.jpg"}),  # poll 2: ready
    ])
    client = PixelfedClient("pixelfed.social", "tok", session=session)
    # Patch sleep to keep the test fast.
    import reachy_mini_meenow.pixelfed as px
    orig = px.time.sleep
    px.time.sleep = lambda *_: None
    try:
        assert client.upload_media(b"x", "alt") == "7"
    finally:
        px.time.sleep = orig
    assert [c[0] for c in session.calls] == ["POST", "GET", "GET"]


def test_post_status_body_is_private_with_tag():
    session = _FakeSession([_FakeResponse(payload={"url": "https://pixelfed.social/p/1"})])
    client = PixelfedClient("pixelfed.social", "tok", session=session)
    url = client.post_status(["42"], "caption here")
    assert url == "https://pixelfed.social/p/1"
    method, endpoint, kwargs = session.calls[0]
    assert method == "POST"
    assert endpoint == "https://pixelfed.social/api/v1/statuses"
    body = kwargs["json"]
    assert body == {
        "status": "caption here\n\n#meenowApp",
        "media_ids": ["42"],
        "visibility": "private",
    }
    assert kwargs["headers"]["Content-Type"] == "application/json"
    # sanity: body is JSON-serialisable
    json.dumps(body)
