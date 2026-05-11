"""Foundation Redis bus: pub/sub + key-value helpers."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Iterator, Optional, Tuple, Union

import redis

from core.config import load_config
from core.logger import get_logger

log = get_logger()

_shared_bus: Union["RedisBus", "LocalBus", None] = None


class RedisBus:
    """Redis-backed pub/sub and string key-value operations."""

    def __init__(self) -> None:
        config = load_config()
        host = config.get("REDIS_HOST", "localhost")
        port = int(config.get("REDIS_PORT", 6379))
        self._client = redis.Redis(host=host, port=port, decode_responses=True)
        self._client.ping()
        log.info(f"Redis connected successfully at {host}:{port}")

    def iter_pattern_messages(self, pattern: str) -> Iterator[Tuple[str, str]]:
        pubsub = self._client.pubsub()
        pubsub.psubscribe(pattern)
        try:
            for msg in pubsub.listen():
                if msg["type"] == "pmessage":
                    yield msg["channel"], msg["data"]
        finally:
            try:
                pubsub.punsubscribe(pattern)
                pubsub.close()
            except redis.RedisError:
                pass

    def publish_json(self, channel: str, data_dict: Any) -> int:
        payload = json.dumps(data_dict)
        return int(self._client.publish(channel, payload))

    def set_str(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        return bool(self._client.set(key, value, ex=ex))

    def get_str(self, key: str) -> Optional[str]:
        return self._client.get(key)

    def scan_count_pattern(self, pattern: str) -> int:
        cursor = 0
        total = 0
        while True:
            cursor, keys = self._client.scan(cursor=cursor, match=pattern, count=256)
            total += len(keys)
            if cursor == 0:
                break
        return total

    def heartbeat(self, name: str) -> None:
        self.set_str(f"heartbeat:{name}", str(time.time()))

    def rpush(self, key: str, *values: str) -> int:
        if not values:
            return int(self._client.llen(key))
        return int(self._client.rpush(key, *[str(v) for v in values]))

    def ltrim(self, key: str, start: int, end: int) -> bool:
        self._client.ltrim(key, start, end)
        return True

    @classmethod
    def from_env(cls):
        return get_shared_bus()


def get_shared_bus():
    """Return the process-wide bus (Redis or :class:`core.local_bus.LocalBus`)."""
    global _shared_bus
    if _shared_bus is not None:
        return _shared_bus

    from core.local_bus import LocalBus

    if os.environ.get("BUS_TYPE", "").strip().lower() == "local":
        log.info("BUS_TYPE=local: using in-process LocalBus")
        _shared_bus = LocalBus()
        return _shared_bus

    try:
        _shared_bus = RedisBus()
        return _shared_bus
    except (ConnectionRefusedError, redis.ConnectionError) as error:
        log.warning(
            "Redis unavailable (%s); falling back to in-process LocalBus. "
            "Start Redis or set BUS_TYPE=local to avoid this.",
            error,
        )
        _shared_bus = LocalBus()
        return _shared_bus


# This function publishes JSON data to a Redis pub/sub channel.
def publish(channel, data):
    bus = get_shared_bus()
    payload = json.dumps(data)
    receivers = bus.publish_json(channel, data)
    log.info(f"publish | channel={channel} | subscribers={receivers} | payload={payload}")
    return receivers


# This function stores a value in Redis key-value storage using JSON serialization.
def set_value(key, value, *, silent: bool = False):
    bus = get_shared_bus()
    payload = json.dumps(value)
    result = bus.set_str(key, payload)
    if not silent:
        log.info(f"set_value | key={key} | success={result} | payload={payload}")
    return result


# This function fetches a key and deserializes JSON back into Python data.
def get_value(key, *, silent: bool = False):
    bus = get_shared_bus()
    raw_value = bus.get_str(key)

    # We return None when key is missing to keep behavior explicit.
    if raw_value is None:
        if not silent:
            log.info(f"get_value | key={key} | value=None")
        return None

    try:
        # We parse JSON string back into Python object.
        parsed_value = json.loads(raw_value)
        if not silent:
            log.info(f"get_value | key={key} | json_loaded=True")
        # We return parsed Python object.
        return parsed_value
    except json.JSONDecodeError:
        # We log fallback behavior when value is not valid JSON.
        log.warning(f"get_value | key={key} | json_loaded=False | returning_raw_string=True")
        # We return raw string to avoid data loss.
        return raw_value
