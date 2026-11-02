"""Tiny HTTP helper (standard library only) with polite rate limiting."""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable

Fetch = Callable[[str, dict[str, str]], bytes]


def http_get(url: str, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


class RateLimited:
    """Wraps a fetch function so calls are at least ``min_interval`` seconds apart."""

    def __init__(self, fetch: Fetch = http_get, min_interval: float = 0.15) -> None:
        self.fetch = fetch
        self.min_interval = min_interval
        self._last = 0.0

    def __call__(self, url: str, headers: dict[str, str]) -> bytes:
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        return self.fetch(url, headers)

    def json(self, url: str, headers: dict[str, str]):
        return json.loads(self(url, headers))
