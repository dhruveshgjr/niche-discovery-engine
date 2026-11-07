"""Shared HTTP plumbing for all sources: one client, polite delays, injectable transport."""
from __future__ import annotations

import time

import httpx

USER_AGENT = "NicheDiscoveryEngine/0.1 (personal market research; macOS)"


class SourceUnavailable(Exception):
    """Raised when a source cannot run (missing credentials, blocked, etc.)."""


class BaseSource:
    name = "base"

    def __init__(
        self,
        client: httpx.Client | None = None,
        delay: float = 1.0,
        timeout: float = 25.0,
    ):
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            trust_env=False,  # no proxies by design; also avoids malformed NO_PROXY env vars
        )
        self._delay = delay

    def collect(self, queries: list[str], limit: int) -> list[dict]:
        raise NotImplementedError

    def _get(self, url: str, **kwargs) -> httpx.Response:
        if self._delay:
            time.sleep(self._delay)
        resp = self._client.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    def close(self) -> None:
        self._client.close()