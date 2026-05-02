from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import redis

KILL_SWITCH_KEY = os.environ.get("QUANT_KILL_KEY", "quant:kill_switch")
HEARTBEAT_PREFIX = os.environ.get("QUANT_HEARTBEAT_PREFIX", "quant:hb:")


class RedisBus:
    def __init__(self, redis_client: redis.Redis) -> None:
        self._r = redis_client

    @classmethod
    def from_env(cls) -> RedisBus:
        host = os.environ.get("REDIS_HOST", "127.0.0.1")
        port = int(os.environ.get("REDIS_PORT", "6379"))
        password = os.environ.get("REDIS_PASSWORD") or None
        client = redis.Redis(host=host, port=port, password=password, decode_responses=True)
        return cls(client)

    def publish_json(self, channel: str, message: dict[str, Any]) -> None:
        self._r.publish(channel, json.dumps(message, default=str))

    def publish_raw(self, channel: str, message: str) -> None:
        self._r.publish(channel, message)

    def iter_channel_messages(self, channel: str) -> Iterator[dict[str, Any]]:
        pubsub = self._r.pubsub()
        pubsub.subscribe(channel)
        for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            raw = msg.get("data")
            if raw is None:
                continue
            yield json.loads(raw)

    def iter_pattern_messages(self, pattern: str) -> Iterator[tuple[str, dict[str, Any]]]:
        pubsub = self._r.pubsub()
        pubsub.psubscribe(pattern)
        for msg in pubsub.listen():
            if msg.get("type") != "pmessage":
                continue
            chan = msg.get("channel")
            raw = msg.get("data")
            if not chan or raw is None:
                continue
            yield chan, json.loads(raw)

    def set_str(self, key: str, value: str, ex: int | None = None) -> None:
        self._r.set(key, value, ex=ex)

    def get_str(self, key: str) -> str | None:
        v = self._r.get(key)
        return None if v is None else str(v)

    def get_int(self, key: str, default: int = 0) -> int:
        v = self.get_str(key)
        if v is None:
            return default
        try:
            return int(float(v))
        except ValueError:
            return default

    def incr(self, key: str, delta: int = 1, ex: int | None = None) -> int:
        n = self._r.incrby(key, delta)
        if ex is not None:
            self._r.expire(key, ex)
        return int(n)

    def scan_count_pattern(self, pattern: str, batch: int = 500) -> int:
        cursor: int | str = 0
        total = 0
        while True:
            cursor, keys = self._r.scan(cursor=cursor, match=pattern, count=batch)
            total += len(keys or [])
            try:
                cursor_i = int(cursor)  # redis-py returns int cursors
            except (TypeError, ValueError):
                cursor_i = cursor
            if cursor_i == 0:
                break
            cursor = cursor_i
        return total

    def heartbeat(self, runner_name: str) -> None:
        self.set_str(f"{HEARTBEAT_PREFIX}{runner_name}", "1", ex=120)
