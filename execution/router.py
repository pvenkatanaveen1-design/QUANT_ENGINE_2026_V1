from __future__ import annotations

import os

from core.bus import RedisBus
from execution import broker_bridge
from logs.trade_journal import log_event
from schemas.order import Order


class ExecutionRouter:
    def __init__(self, bus: RedisBus, position_ttl_sec: int = 86400 * 14) -> None:
        self._bus = bus
        self._ttl = position_ttl_sec

    def dispatch(self, order: Order) -> broker_bridge.BridgeResult:
        res = broker_bridge.send_market(order)
        broker_bridge.annotate_order_sent(order, res)
        log_event(
            "execution",
            {
                "ok": res.ok,
                "ticket": res.ticket,
                "symbol": order.symbol,
                "side": order.side.value,
                "volume_lots": order.volume_lots,
                "comment": order.comment,
                "bridge_comment": res.comment,
            },
        )
        if res.ok and res.ticket is not None:
            ttl = int(os.environ.get("QUANT_POS_TTL_SEC", str(self._ttl)))
            self._bus.set_str(f"quant:pos:{res.ticket}", order.symbol, ex=ttl)
        return res
