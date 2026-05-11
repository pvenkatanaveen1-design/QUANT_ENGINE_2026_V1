"""
Live MT5 position monitoring — DEMO only (Phase 14).

Polls `positions_get()`, publishes `orders:*` + `positions:summary` to Redis.
No modifications, trailing, or auto-close — read-only infrastructure.
"""

from __future__ import annotations

import time
from typing import Any

from core.bus import set_value
from core.logger import get_logger

log = get_logger()

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None  # type: ignore[assignment]

ORDER_TRACKER_INTERVAL_SECONDS = 3.0


def initialize_mt5() -> bool:
    """Reuse an existing MT5 session or connect via `core.pulse.initialize_mt5`."""
    if mt5 is None:
        log.error("order_tracker | MetaTrader5 package not installed")
        return False
    try:
        if mt5.terminal_info() is not None:
            log.debug("order_tracker | MT5 session active — reusing")
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("order_tracker | terminal_info | {}", exc)

    from core.pulse import initialize_mt5 as pulse_initialize_mt5  # noqa: PLC0415

    return bool(pulse_initialize_mt5())


def ensure_demo_account() -> tuple[bool, str]:
    """Phase 14 is DEMO-only — refuse live/funded accounts."""
    if mt5 is None:
        return False, "MetaTrader5 not available"
    acc = mt5.account_info()
    if acc is None:
        return False, "account_info() returned None — is the terminal logged in?"
    demo = int(getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 1))
    if int(getattr(acc, "trade_mode", -1)) != demo:
        return False, "Not a DEMO account — order tracker disabled (Phase 14 safety)"
    return True, ""


def _position_type_side(p: Any) -> str:
    if mt5 is None:
        return "UNKNOWN"
    buy = int(getattr(mt5, "POSITION_TYPE_BUY", 0))
    return "BUY" if int(getattr(p, "type", -1)) == buy else "SELL"


def fetch_open_positions() -> list[dict[str, Any]]:
    """
    Return normalized open-position dicts from MT5.

    Empty list when none or on fetch failure (errors are logged).
    """
    if mt5 is None:
        return []
    try:
        raw = mt5.positions_get()
    except Exception as exc:  # noqa: BLE001
        log.warning("order_tracker | positions_get failed | {}", exc)
        return []

    if not raw:
        return []

    out: list[dict[str, Any]] = []
    for p in raw:
        try:
            ticket = int(getattr(p, "ticket", 0))
            sym = str(getattr(p, "symbol", "") or "")
            vol = float(getattr(p, "volume", 0.0) or 0.0)
            entry = float(getattr(p, "price_open", 0.0) or 0.0)
            current = float(getattr(p, "price_current", 0.0) or 0.0)
            sl = float(getattr(p, "sl", 0.0) or 0.0)
            tp = float(getattr(p, "tp", 0.0) or 0.0)
            profit = float(getattr(p, "profit", 0.0) or 0.0)
            t_open = int(getattr(p, "time", 0) or 0)
            t_msc = getattr(p, "time_msc", None)
            if t_msc is not None:
                t_open = int(t_msc // 1000)

            out.append(
                {
                    "ticket": ticket,
                    "symbol": sym.upper(),
                    "side": _position_type_side(p),
                    "volume": vol,
                    "entry_price": entry,
                    "current_price": current,
                    "sl": sl,
                    "tp": tp,
                    "floating_profit": profit,
                    "open_time": t_open,
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("order_tracker | skip malformed position | {}", exc)
    return out


def calculate_total_floating_pnl(positions: list[dict[str, Any]]) -> float:
    """Sum `floating_profit` across all open positions (account currency)."""
    total = 0.0
    for p in positions:
        try:
            total += float(p.get("floating_profit") or 0.0)
        except (TypeError, ValueError):
            continue
    return round(total, 8)


def build_position_summary(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate stats + copy of positions for Redis JSON.

    Keys match dashboard / future trade-management consumers.
    """
    now = time.time()
    total_pnl = calculate_total_floating_pnl(positions)
    winners = sum(1 for p in positions if float(p.get("floating_profit") or 0) > 0)
    losers = sum(1 for p in positions if float(p.get("floating_profit") or 0) < 0)

    return {
        "active_count": len(positions),
        "total_floating_pnl": total_pnl,
        "winning_trades": winners,
        "losing_trades": losers,
        "tracker_status": "RUNNING",
        "positions": list(positions),
        "updated_at": now,
    }


def publish_position_updates(summary: dict[str, Any]) -> None:
    """
    Write `orders:*` headline keys + `positions:summary` JSON bundle.

    `summary` is typically from `build_position_summary` or an error snapshot.
    """
    now = float(summary.get("updated_at") or time.time())
    positions = list(summary.get("positions") or [])
    count = int(summary.get("active_count") or 0)

    last_sym = None
    last_ticket = None
    last_type = None
    last_vol: float | None = None
    last_profit: float | None = None
    last_st = "NO_POSITIONS"

    if positions:
        # "Last" = highest ticket (usually most recently opened in practice).
        lead = max(positions, key=lambda x: int(x.get("ticket") or 0))
        last_sym = lead.get("symbol")
        last_ticket = int(lead.get("ticket") or 0)
        last_type = lead.get("side")
        last_vol = float(lead.get("volume") or 0.0)
        last_profit = float(lead.get("floating_profit") or 0.0)
        last_st = "OPEN"
    elif summary.get("tracker_status") not in (None, "RUNNING"):
        last_st = "ERROR"

    try:
        set_value("orders:active_count", count)
        set_value("orders:last_symbol", last_sym)
        set_value("orders:last_ticket", last_ticket)
        set_value("orders:last_type", last_type)
        set_value("orders:last_volume", last_vol)
        set_value("orders:last_profit", last_profit)
        set_value("orders:last_status", last_st)
        set_value("orders:last_update", now)
        set_value("positions:summary", summary)
        log.info(
            "order_tracker | publish | count={} pnl={} status={}",
            count,
            summary.get("total_floating_pnl"),
            last_st,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("order_tracker | publish failed | {}", exc)


def publish_tracker_unavailable(tracker_status: str, message: str) -> None:
    """Publish a safe empty snapshot when MT5/demo checks fail."""
    now = time.time()
    summary = {
        "active_count": 0,
        "total_floating_pnl": 0.0,
        "winning_trades": 0,
        "losing_trades": 0,
        "tracker_status": tracker_status,
        "error_message": message,
        "positions": [],
        "updated_at": now,
    }
    publish_position_updates(summary)


def run_order_tracker() -> None:
    """
    Background loop (`run.py`): refresh open-position snapshot every few seconds.

    Stays RUNNING even when MT5 is briefly unavailable — publishes ERROR/NO_MT5 snapshots.
    """
    log.info(
        "order_tracker | daemon start | interval_s={}",
        ORDER_TRACKER_INTERVAL_SECONDS,
    )
    while True:
        try:
            if not initialize_mt5():
                publish_tracker_unavailable("NO_MT5", "MT5 not available — check terminal / pulse")
                time.sleep(5.0)
                continue

            ok, msg = ensure_demo_account()
            if not ok:
                publish_tracker_unavailable("NOT_DEMO", msg)
                time.sleep(5.0)
                continue

            positions = fetch_open_positions()
            summary = build_position_summary(positions)
            publish_position_updates(summary)
        except Exception as exc:  # noqa: BLE001
            log.exception("order_tracker | tick failed")
            publish_tracker_unavailable("ERROR", str(exc))

        time.sleep(ORDER_TRACKER_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_order_tracker()
