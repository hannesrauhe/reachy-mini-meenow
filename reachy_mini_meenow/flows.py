"""Smart-home flow-builder client (raspi freeps server).

Two endpoints on the flow builder:

- ``GET  /flowbuilder/ListFlows`` -> ``{name: {DisplayName, Description, Kind,
  Tags}}`` — the catalogue the LLM picks from.
- ``POST /flow/<name>`` — fire the flow (no body).

Same injectable-client idiom as :class:`~reachy_mini_meenow.pixelfed.PixelfedClient`
so the request shape is unit-testable without the raspi.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

_TIMEOUT_S = 15.0


class FlowClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=_TIMEOUT_S, trust_env=True)

    def list_flows(self) -> dict:
        """Return the flow catalogue ``{name: {DisplayName, Description, Kind, ...}}``."""
        res = self.client.get(f"{self.base_url}/flowbuilder/ListFlows")
        res.raise_for_status()
        data = res.json()
        if not isinstance(data, dict):
            raise TypeError("ListFlows returned unexpected JSON")
        return data

    def run_flow(self, name: str) -> bool:
        """Fire a flow by name; True on a 2xx response."""
        res = self.client.post(f"{self.base_url}/flow/{quote(name)}")
        ok = res.status_code < 400
        if not ok:
            log.warning("run_flow(%s) failed: HTTP %s", name, res.status_code)
        return ok
