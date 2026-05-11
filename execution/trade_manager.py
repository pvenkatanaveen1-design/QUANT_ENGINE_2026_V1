"""
Simulated trade lifecycle management — DEMO only (Phase 15).

Reads live position context (MT5 + Redis mirrors), evaluates simple R-multiple rules,
and publishes **simulated** management intents to Redis. No SL/TP updates, no closes.
"""

from __future__ import annotations

import time
from typing import Any

from core.bus import get_value, set_value
from core.config import load_config
from core.logger import get_logger
from execution.order_tracker import (
    ensure_demo_account,
    fetch_open_positions as _mt5_fetch_open_positions,
    initialize_mt5,
)

log = get_logger()

TRADE_MANAGER_INTERVAL_SECONDS = 4.0

ACTION_MOVE_SL_BREAKEVEN = "MOVE_SL_TO_BREAKEVEN"
ACTION_TRAIL_STOP = "TRAIL_STOP"
ACTION_PARTIAL_CLOSE = "PARTIAL_CLOSE"
ACTION_HOLD = "HOLD_POSITION"

EVENTS_KEY = "trade_manager:events"
EVENTS_MAX = 50


def fetch_open_positions() -> list[dict[str, Any]]:
    """
    Open positions from MT5 (DEMO path only).

    Returns [] when MT5 is unavailable or account is not DEMO — same shape as order_tracker.
    """
    if not initialize_mt5():
        return []
    ok, _msg = ensure_demo_account()
    if not ok:
        return []
    return _mt5_fetch_open_positions()


def simulate_breakeven_logic(floating_profit: float, r_unit: float) -> tuple[bool, str]:
    """True when profit strictly exceeds 1R (simulation only — no broker modify)."""
    if r_unit <= 0:
        return False, "invalid R unit"
    if floating_profit > r_unit:
        return True, f"floating_profit {floating_profit:.2f} > 1R ({r_unit:.2f})"
    return False, ""


def simulate_trailing_logic(floating_profit: float, r_unit: float) -> tuple[bool, str]:
    """True when profit strictly exceeds 2R."""
    if r_unit <= 0:
        return False, "invalid R unit"
    if floating_profit > 2.0 * r_unit:
        return True, f"floating_profit {floating_profit:.2f} > 2R ({2.0 * r_unit:.2f})"
    return False, ""


def simulate_partial_close_logic(floating_profit: float, r_unit: float) -> tuple[bool, str]:
    """True when profit strictly exceeds 3R."""
    if r_unit <= 0:
        return False, "invalid R unit"
    if floating_profit > 3.0 * r_unit:
        return True, f"floating_profit {floating_profit:.2f} > 3R ({3.0 * r_unit:.2f})"
    return False, ""


def evaluate_trade_management(
    positions: list[dict[str, Any]],
    r_unit: float | None = None,
) -> dict[str, Any]:
    """
    Pick the lead position (highest ticket) and return a simulated management decision.

    Tier order (highest wins): PARTIAL_CLOSE > TRAIL_STOP > MOVE_SL_TO_BREAKEVEN > HOLD_POSITION.
    """
    if r_unit is None:
        try:
            r_unit = float(load_config().get("TRADE_MANAGER_R_UNIT", 10.0))
        except (TypeError, ValueError):
            r_unit = 10.0

    if not positions:
        return {
            "ticket": None,
            "symbol": None,
            "action": ACTION_HOLD,
            "reason": "NO_OPEN_POSITIONS",
            "floating_profit": 0.0,
            "r_unit": r_unit,
        }

    lead = max(positions, key=lambda x: int(x.get("ticket") or 0))
    ticket = int(lead.get("ticket") or 0)
    sym = str(lead.get("symbol") or "").upper() or None
    try:
        fp = float(lead.get("floating_profit") or 0.0)
    except (TypeError, ValueError):
        fp = 0.0

    ok_p, reason_p = simulate_partial_close_logic(fp, r_unit)
    if ok_p:
        return {
            "ticket": ticket,
            "symbol": sym,
            "action": ACTION_PARTIAL_CLOSE,
            "reason": f"SIMULATE {ACTION_PARTIAL_CLOSE}: {reason_p}",
            "floating_profit": fp,
            "r_unit": r_unit,
        }

    ok_t, reason_t = simulate_trailing_logic(fp, r_unit)
    if ok_t:
        return {
            "ticket": ticket,
            "symbol": sym,
            "action": ACTION_TRAIL_STOP,
            "reason": f"SIMULATE {ACTION_TRAIL_STOP}: {reason_t}",
            "floating_profit": fp,
            "r_unit": r_unit,
        }

    ok_b, reason_b = simulate_breakeven_logic(fp, r_unit)
    if ok_b:
        return {
            "ticket": ticket,
            "symbol": sym,
            "action": ACTION_MOVE_SL_BREAKEVEN,
            "reason": f"SIMULATE {ACTION_MOVE_SL_BREAKEVEN}: {reason_b}",
            "floating_profit": fp,
            "r_unit": r_unit,
        }

    return {
        "ticket": ticket,
        "symbol": sym,
        "action": ACTION_HOLD,
        "reason": (
            f"SIMULATE {ACTION_HOLD}: no tier triggered (pnl={fp:.2f}, need >1R / >2R / >3R vs "
            f"R_unit={r_unit:.2f})"
        ),
        "floating_profit": fp,
        "r_unit": r_unit,
    }


def _coalesce_prior_event_fields() -> tuple[Any, Any, Any]:
    """Read last published manager fields from Redis for dedupe."""
    try:
        return (
            get_value("trade_manager:last_ticket"),
            get_value("trade_manager:last_action"),
            get_value("trade_manager:last_reason"),
        )
    except Exception:  # noqa: BLE001
        return None, None, None


def _load_events() -> list[dict[str, Any]]:
    raw = get_value(EVENTS_KEY)
    if isinstance(raw, list):
        out: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                out.append(dict(item))
        return out
    return []


def publish_trade_management_status(decision: dict[str, Any], *, manager_status: str) -> None:
    """
    Write `trade_manager:*` headline keys plus rolling `trade_manager:events` (newest first).

    Appends an event row when (ticket, action, reason) changes vs the last Redis snapshot.
    """
    now = time.time()
    ticket = decision.get("ticket")
    symbol = decision.get("symbol")
    action = str(decision.get("action") or ACTION_HOLD)
    reason = str(decision.get("reason") or "")

    prev_ticket, prev_action, prev_reason = _coalesce_prior_event_fields()
    signature_changed = (prev_ticket, prev_action, prev_reason) != (ticket, action, reason)

    events = _load_events()
    if signature_changed:
        events.insert(
            0,
            {
                "ticket": ticket,
                "symbol": symbol,
                "action": action,
                "reason": reason,
                "timestamp": now,
            },
        )
        events = events[:EVENTS_MAX]

    payload = {
        "trade_manager:last_ticket": ticket,
        "trade_manager:last_symbol": symbol,
        "trade_manager:last_action": action,
        "trade_manager:last_reason": reason,
        "trade_manager:last_update": now,
        "trade_manager:status": str(manager_status),
    }
    try:
        for key, val in payload.items():
            set_value(key, val)
        set_value(EVENTS_KEY, events)
        log.info(
            "trade_manager | publish | status={} ticket={} action={}",
            manager_status,
            ticket,
            action,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("trade_manager | publish failed | {}", exc)


def run_trade_manager() -> None:
    """
    Background loop: DEMO MT5 positions + Redis mirrors → simulated management decisions.
    """
    log.info(
        "trade_manager | daemon start | interval_s={} | DEMO simulation only (no SL/TP orders)",
        TRADE_MANAGER_INTERVAL_SECONDS,
    )
    while True:
        try:
            if not initialize_mt5():
                publish_trade_management_status(
                    {
                        "ticket": None,
                        "symbol": None,
                        "action": ACTION_HOLD,
                        "reason": "MT5 not available — cannot evaluate positions",
                        "floating_profit": 0.0,
                        "r_unit": float(load_config().get("TRADE_MANAGER_R_UNIT", 10.0)),
                    },
                    manager_status="NO_MT5",
                )
                time.sleep(5.0)
                continue

            ok, msg = ensure_demo_account()
            if not ok:
                publish_trade_management_status(
                    {
                        "ticket": None,
                        "symbol": None,
                        "action": ACTION_HOLD,
                        "reason": msg or "Not a DEMO account",
                        "floating_profit": 0.0,
                        "r_unit": float(load_config().get("TRADE_MANAGER_R_UNIT", 10.0)),
                    },
                    manager_status="NOT_DEMO",
                )
                time.sleep(5.0)
                continue

            # Required Redis reads (mirrors order_tracker; used for coherence checks).
            summary = get_value("positions:summary")
            redis_orders_count = get_value("orders:active_count")
            positions = fetch_open_positions()
            mt5_n = len(positions)

            if redis_orders_count is not None:
                try:
                    roc = int(float(redis_orders_count))
                    if roc != mt5_n:
                        log.warning(
                            "trade_manager | orders:active_count={} vs mt5 positions={} — using MT5 list",
                            roc,
                            mt5_n,
                        )
                except (TypeError, ValueError):
                    pass

            if isinstance(summary, dict):
                try:
                    sc = int(summary.get("active_count") or 0)
                    if sc != mt5_n:
                        log.warning(
                            "trade_manager | positions:summary active_count={} vs mt5={}",
                            sc,
                            mt5_n,
                        )
                except (TypeError, ValueError):
                    pass

            cfg = load_config()
            try:
                r_unit = float(cfg.get("TRADE_MANAGER_R_UNIT", 10.0))
            except (TypeError, ValueError):
                r_unit = 10.0

            decision = evaluate_trade_management(positions, r_unit=r_unit)
            mgr_st = "NO_POSITIONS" if mt5_n == 0 else "RUNNING"
            publish_trade_management_status(decision, manager_status=mgr_st)

        except Exception as exc:  # noqa: BLE001
            log.exception("trade_manager | tick failed")
            try:
                r_unit = float(load_config().get("TRADE_MANAGER_R_UNIT", 10.0))
            except (TypeError, ValueError):
                r_unit = 10.0
            publish_trade_management_status(
                {
                    "ticket": None,
                    "symbol": None,
                    "action": ACTION_HOLD,
                    "reason": str(exc),
                    "floating_profit": 0.0,
                    "r_unit": r_unit,
                },
                manager_status="ERROR",
            )

        time.sleep(TRADE_MANAGER_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_trade_manager()
