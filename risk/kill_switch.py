from __future__ import annotations

from core.bus import KILL_SWITCH_KEY, RedisBus


def is_halted(bus: RedisBus) -> bool:
    v = bus.get_str(KILL_SWITCH_KEY)
    return v in ("1", "true", "True", "yes", "YES")


def halt(bus: RedisBus, reason: str) -> None:
    bus.set_str(KILL_SWITCH_KEY, "1", ex=None)
    bus.set_str("quant:kill_reason", reason, ex=3600 * 24 * 30)
