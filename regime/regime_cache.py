from __future__ import annotations

import time


class RegimeAnalyticsCache:
    def __init__(self, ttl_sec: int = 120) -> None:
        self._ttl_sec = ttl_sec
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        row = self._store.get(key)
        if not row:
            return None
        ts, payload = row
        if time.time() - ts > self._ttl_sec:
            self._store.pop(key, None)
            return None
        return payload

    def set(self, key: str, payload: object) -> None:
        self._store[key] = (time.time(), payload)

