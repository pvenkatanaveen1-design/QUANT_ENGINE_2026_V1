from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass

try:
    import MetaTrader5 as mt5  # type: ignore
except ImportError:
    mt5 = None

from schemas.order import Order, OrderSide, OrderStatus

_MT5_READY = False


@dataclass(frozen=True)
class BridgeResult:
    ok: bool
    ticket: int | None
    comment: str


def _simulate_mode() -> bool:
    return os.environ.get("QUANT_MT5_SIMULATE", "0") == "1" or mt5 is None


def account_equity() -> float:
    if _simulate_mode():
        return float(os.environ.get("QUANT_DEMO_EQUITY", "100000"))
    _ensure_mt5()
    info = mt5.account_info()
    return float(info.equity) if info else float(os.environ.get("QUANT_DEMO_EQUITY", "100000"))


def _ensure_mt5() -> None:
    global _MT5_READY
    if _MT5_READY:
        return
    if mt5 is None:
        raise RuntimeError("MetaTrader5 not installed")

    login = int(os.environ.get("MT5_LOGIN", "0"))
    password = os.environ.get("MT5_PASSWORD", "")
    server = os.environ.get("MT5_SERVER", "")

    initialized = False
    if login and password and server:
        initialized = bool(mt5.initialize(login=login, password=password, server=server))

    if not initialized and not mt5.initialize():
        raise RuntimeError(f"MT5 init failed {mt5.last_error()}")

    if login and password and server:
        if not mt5.login(login=login, password=password, server=server):
            raise RuntimeError(f"MT5 login failed {mt5.last_error()}")

    _MT5_READY = True


def send_market(order: Order) -> BridgeResult:
    if _simulate_mode():
        ticket = random.randint(200_000, 900_000)
        time.sleep(0.01)
        return BridgeResult(True, ticket, "simulated_fill")

    _ensure_mt5()
    magic = order.magic
    comment = order.comment[:31]
    filling = getattr(mt5, "ORDER_FILLING_FOK", 0)

    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": order.symbol,
        "volume": float(order.volume_lots),
        "type": mt5.ORDER_TYPE_BUY if order.side == OrderSide.BUY else mt5.ORDER_TYPE_SELL,
        "sl": float(order.sl_price or 0.0),
        "tp": float(order.tp_price or 0.0),
        "magic": int(magic),
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "deviation": int(os.environ.get("MT5_DEVIATION_POINTS", "30")),
        "type_filling": filling,
    }
    res = mt5.order_send(req)
    accepted_codes = {getattr(mt5, "TRADE_RETCODE_DONE", 10009)}
    placed = getattr(mt5, "TRADE_RETCODE_PLACED", None)
    if isinstance(placed, int):
        accepted_codes.add(placed)
    if res is None or res.retcode not in accepted_codes:
        msg = getattr(res, "comment", "") if res is not None else str(mt5.last_error())
        return BridgeResult(False, None, msg)
    ticket = int(getattr(res, "deal", getattr(res, "order", 0)) or 0) or None
    return BridgeResult(True, ticket, getattr(res, "comment", "") or "")


def annotate_order_sent(order: Order, result: BridgeResult) -> Order:
    status = OrderStatus.FILLED if result.ok else OrderStatus.REJECTED
    order.status = status
    order.broker_ticket = result.ticket
    return order
