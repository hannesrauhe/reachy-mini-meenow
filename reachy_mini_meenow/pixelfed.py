"""Pixelfed / Mastodon-compatible posting client.

Ports the media-upload and status-creation flow from meenow's ``src/api/pixelfed.ts``
so that posts are indistinguishable from the PWA's: a followers-only
(``visibility: private``) status carrying the ``#meenowApp`` tag and at least one
media attachment.

Uses HTTP/2 (via httpx). Pixelfed access tokens are large (~1 KB RS256 JWTs); some
instances' edge (e.g. gram.social) reject that uncompressed ``Authorization`` header
over HTTP/1.1 with a 400 "Request Header Or Cookie Too Large", but accept it over
HTTP/2 where HPACK compresses it. httpx negotiates HTTP/2 by ALPN and falls back to
HTTP/1.1 for instances that do not offer it.
"""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

MEENOW_TAG = "#meenowApp"
_MEDIA_POLL_ATTEMPTS = 20
_MEDIA_POLL_INTERVAL_S = 1.5
_TIMEOUT_S = 30.0


class PixelfedClient:
    def __init__(self, instance: str, token: str, client: httpx.Client | None = None):
        self.instance = instance
        self.token = token
        self.client = client or httpx.Client(
            http2=True, timeout=_TIMEOUT_S, trust_env=True
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _url(self, path: str) -> str:
        return f"https://{self.instance}{path}"

    def verify_credentials(self) -> str | None:
        """Return the authenticated account id, or ``None`` on failure (non-fatal)."""
        try:
            res = self.client.get(
                self._url("/api/v1/accounts/verify_credentials"), headers=self._headers
            )
            if res.status_code >= 400:
                log.warning("verify_credentials failed (%s)", res.status_code)
                return None
            return res.json().get("id")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("verify_credentials failed: %s", exc)
            return None

    def upload_media(self, jpeg: bytes, description: str) -> str:
        """Upload a JPEG and return its media id, polling until processing completes."""
        res = self.client.post(
            self._url("/api/v1/media"),
            headers=self._headers,
            files={"file": ("meenow.jpg", jpeg, "image/jpeg")},
            data={"description": description},
        )
        if res.status_code >= 400:
            raise RuntimeError(f"Media upload failed ({res.status_code})")
        media = res.json()
        media_id = media["id"]
        if media.get("url") is not None:
            return media_id

        for _ in range(_MEDIA_POLL_ATTEMPTS):
            time.sleep(_MEDIA_POLL_INTERVAL_S)
            poll = self.client.get(
                self._url(f"/api/v1/media/{media_id}"), headers=self._headers
            )
            if poll.status_code >= 400:
                raise RuntimeError(f"Media poll failed ({poll.status_code})")
            if poll.json().get("url") is not None:
                return media_id
        raise RuntimeError("Media processing timed out")

    def post_status(self, media_ids: list[str], caption: str | None) -> str:
        """Create a followers-only status tagged ``#meenowApp``; return its URL."""
        status = build_status_text(caption)
        res = self.client.post(
            self._url("/api/v1/statuses"),
            headers=self._headers,
            json={"status": status, "media_ids": media_ids, "visibility": "private"},
        )
        if res.status_code >= 400:
            raise RuntimeError(f"Post failed ({res.status_code})")
        return res.json().get("url", "")

    def post_photo(self, jpeg: bytes, caption: str | None, alt: str) -> str:
        media_id = self.upload_media(jpeg, alt)
        return self.post_status([media_id], caption)

    def post_meenow(self, composite: bytes, back: bytes, front: bytes,
                    caption: str | None) -> str:
        """Post the stitched composite plus both source photos, like the PWA's postMeenow.

        The composite is uploaded first so it gets the lowest attachment id and
        appears first in the gallery; alt texts match the PWA's.
        """
        composite_id = self.upload_media(composite, "meenow — daily photo")
        back_id = self.upload_media(back, "meenow — surroundings")
        front_id = self.upload_media(front, "meenow — selfie")
        return self.post_status([composite_id, back_id, front_id], caption)


def build_status_text(caption: str | None) -> str:
    caption = (caption or "").strip()
    return f"{caption}\n\n{MEENOW_TAG}" if caption else MEENOW_TAG
