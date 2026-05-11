"""In-process pub/sub and key-value store mirroring :class:`core.bus.RedisBus`."""

from __future__ import annotations

import fnmatch
import json
import queue
import threading
import time
from typing import Any, Iterator, Optional, Tuple


class LocalBus:
    """Pub/sub via :class:`queue.Queue`, string KV with TTL, and in-memory list keys."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Tuple[str, str]] = queue.Queue()
        self._store: dict[str, tuple[str, Optional[float]]] = {}
        self._lists: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def _purge_expired_unlocked(self) -> None:
        now = time.monotonic()
        for key, (_, exp) in list(self._store.items()):
            if exp is not None and now > exp:
                del self._store[key]

    def iter_pattern_messages(self, pattern: str) -> Iterator[Tuple[str, str]]:
        while True:
            channel, payload = self._queue.get()
            if fnmatch.fnmatch(channel, pattern):
                yield channel, payload

    def publish_json(self, channel: str, data_dict: Any) -> int:
        payload = json.dumps(data_dict)
        self._queue.put((channel, payload))
        return 0

    def set_str(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        with self._lock:
            self._purge_expired_unlocked()
            exp_ts = time.monotonic() + ex if ex is not None else None
            self._store[key] = (value, exp_ts)
        return True

    def get_str(self, key: str) -> Optional[str]:
        with self._lock:
            self._purge_expired_unlocked()
            rec = self._store.get(key)
            if rec is None:
                return None
            val, exp = rec
            if exp is not None and time.monotonic() > exp:
                del self._store[key]
                return None
            return val

    def scan_count_pattern(self, pattern: str) -> int:
        with self._lock:
            self._purge_expired_unlocked()
            return sum(1 for k in self._store if fnmatch.fnmatch(k, pattern))

    def heartbeat(self, name: str) -> None:
        self.set_str(f"heartbeat:{name}", str(time.time()))

    def rpush(self, key: str, *values: str) -> int:
        if not values:
            with self._lock:
                return len(self._lists.get(key, []))
        with self._lock:
            lst = self._lists.setdefault(key, [])
            for v in values:
                lst.append(str(v))
            return len(lst)

    def ltrim(self, key: str, start: int, end: int) -> bool:
        """Trim list ``key`` to inclusive Redis-style ``start``..``end`` indices."""
        with self._lock:
            lst = self._lists.get(key)
            if not lst:
                return True
            n = len(lst)

            def resolve(i: int) -> int:
                if i < 0:
                    i += n
                return i

            s, e = resolve(start), resolve(end)
            s = max(0, min(s, n - 1))
            e = max(0, min(e, n - 1))
            if s > e:
                self._lists[key] = []
            else:
                self._lists[key] = lst[s : e + 1]
            return True

    @classmethod
    def from_env(cls):
        from core.bus import get_shared_bus

        return get_shared_bus()
