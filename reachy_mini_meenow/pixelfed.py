"""Pixelfed / Mastodon-compatible posting client.

Ports the media-upload and status-creation flow from meenow's ``src/api/pixelfed.ts``
so that posts are indistinguishable from the PWA's: a followers-only
(``visibility: private``) status carrying the ``#meenowApp`` tag and at least one
media attachment.
"""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

MEENOW_TAG = "#meenowApp"
_MEDIA_POLL_ATTEMPTS = 20
_MEDIA_POLL_INTERVAL_S = 1.5
_TIMEOUT_S = 30


class PixelfedClient:
    def __init__(self, instance: str, token: str, session: requests.Session | None = None):
        self.instance = instance
        self.token = token
        self.session = session or requests.Session()

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _url(self, path: str) -> str:
        return f"https://{self.instance}{path}"

    def verify_credentials(self) -> str | None:
        """Return the authenticated account id, or ``None`` on failure (non-fatal)."""
        try:
            res = self.session.get(
                self._url("/api/v1/accounts/verify_credentials"),
                headers=self._headers,
                timeout=_TIMEOUT_S,
            )
            res.raise_for_status()
            return res.json().get("id")
        except (requests.RequestException, ValueError) as exc:
            log.warning("verify_credentials failed: %s", exc)
            return None

    def upload_media(self, jpeg: bytes, description: str) -> str:
        """Upload a JPEG and return its media id, polling until processing completes."""
        res = self.session.post(
            self._url("/api/v1/media"),
            headers=self._headers,
            files={"file": ("meenow.jpg", jpeg, "image/jpeg")},
            data={"description": description},
            timeout=_TIMEOUT_S,
        )
        if not res.ok:
            raise RuntimeError(f"Media upload failed ({res.status_code})")
        media = res.json()
        media_id = media["id"]
        if media.get("url") is not None:
            return media_id

        for _ in range(_MEDIA_POLL_ATTEMPTS):
            time.sleep(_MEDIA_POLL_INTERVAL_S)
            poll = self.session.get(
                self._url(f"/api/v1/media/{media_id}"),
                headers=self._headers,
                timeout=_TIMEOUT_S,
            )
            if not poll.ok:
                raise RuntimeError(f"Media poll failed ({poll.status_code})")
            if poll.json().get("url") is not None:
                return media_id
        raise RuntimeError("Media processing timed out")

    def post_status(self, media_ids: list[str], caption: str | None) -> str:
        """Create a followers-only status tagged ``#meenowApp``; return its URL."""
        status = build_status_text(caption)
        res = self.session.post(
            self._url("/api/v1/statuses"),
            headers={**self._headers, "Content-Type": "application/json"},
            json={"status": status, "media_ids": media_ids, "visibility": "private"},
            timeout=_TIMEOUT_S,
        )
        if not res.ok:
            raise RuntimeError(f"Post failed ({res.status_code})")
        return res.json().get("url", "")

    def post_photo(self, jpeg: bytes, caption: str | None, alt: str) -> str:
        media_id = self.upload_media(jpeg, alt)
        return self.post_status([media_id], caption)


def build_status_text(caption: str | None) -> str:
    caption = (caption or "").strip()
    return f"{caption}\n\n{MEENOW_TAG}" if caption else MEENOW_TAG
