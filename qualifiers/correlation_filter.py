from __future__ import annotations

from core.bus import RedisBus


def open_positions_snapshot_count(bus: RedisBus) -> int:
    return bus.scan_count_pattern("quant:pos:*")


def correlation_ok(bus: RedisBus, max_open_positions: int) -> bool:
    return open_positions_snapshot_count(bus) < max_open_positions
