"""
Broker bridge scenarios — DEMO + TEST mode only; uses volume 0.01.

Requires:
  - Redis
  - MetaTrader 5 terminal + Python package
  - `.env` SYSTEM_MODE=TEST and demo credentials

From project root:

    python scripts/test_broker_bridge.py
"""

from __future__ import annotations

from pathlib import Path
import sys
import time

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.bus import get_value, set_value
from execution.broker_bridge import ALLOWED_VOLUME, send_market_order
from execution.router import evaluate_trade_request
from risk.cost_guard import evaluate_cost_guard_once


def push_market(symbol: str, bid: float, ask: float) -> None:
    sym = str(symbol).upper()
    spr = abs(float(ask) - float(bid))
    set_value(f"market:{sym}:bid", bid)
    set_value(f"market:{sym}:ask", ask)
    set_value(f"market:{sym}:spread", spr)


def tight_all() -> None:
    for sym, b, a in [
        ("EURUSD", 1.08500, 1.08501),
        ("GBPUSD", 1.26500, 1.26501),
        ("USDJPY", 150.000, 150.010),
        ("XAUUSD", 2650.00, 2650.01),
        ("BTCUSD", 95000.00, 95005.00),
    ]:
        push_market(sym, b, a)


def base_safety_green() -> None:
    set_value("heartbeat:overall", "HEALTHY")
    set_value("risk:block_trading", False)
    set_value("kill_switch:active", False)
    set_value("news:blackout", "INACTIVE")
    set_value("news:blackout_reason", "test_broker_bridge baseline")
    set_value("clock:session", "London-New York overlap")
    set_value("SYSTEM_MODE", "TEST")


def approve_router_for(symbol: str) -> None:
    """Make router publish APPROVED for `symbol` (matches live cost guard inputs)."""
    base_safety_green()
    tight_all()
    push_market(symbol, 1.10000, 1.10008)
    evaluate_cost_guard_once()
    evaluate_trade_request(str(symbol).upper())


def print_snapshot(title: str) -> None:
    print("")
    print("==============================================")
    print(title)
    print("==============================================")
    print(f"  execution:last_symbol    = {get_value('execution:last_symbol')}")
    print(f"  execution:last_side      = {get_value('execution:last_side')}")
    print(f"  execution:last_volume    = {get_value('execution:last_volume')}")
    print(f"  execution:last_status    = {get_value('execution:last_status')}")
    print(f"  execution:last_ticket    = {get_value('execution:last_ticket')}")
    print(f"  execution:last_reason    = {get_value('execution:last_reason')}")
    print(f"  execution:bridge_status  = {get_value('execution:bridge_status')}")


def main() -> None:
    print("QUANT_ENGINE_2026 | execution.broker_bridge | scripts/test_broker_bridge.py")
    print("Phase 12: DEMO account + SYSTEM_MODE=TEST only. Volume fixed at 0.01 lots.")
    sym = "EURUSD"

    # --- 1) Demo market order path (may hit MT5 — confirm you want this on your terminal) ---
    print_snapshot("1) Router APPROVED seed -> send_market_order EURUSD BUY 0.01")
    approve_router_for(sym)
    print("  >> calling send_market_order (real demo order_send if env allows)...")
    r = send_market_order(sym, "BUY", ALLOWED_VOLUME)
    print(f"  result: ok={r.get('ok')} status={r.get('status')} ticket={r.get('ticket')}")
    print(f"  reason: {r.get('reason')}")
    print_snapshot("After scenario 1")

    # --- 2) Router mismatch / not approved ---
    print_snapshot("2) BLOCKED: router decision not APPROVED")
    base_safety_green()
    tight_all()
    evaluate_cost_guard_once()
    set_value("router:last_symbol", sym)
    set_value("router:last_decision", "BLOCKED")
    set_value("router:last_reason", "forced BLOCKED for test")
    set_value("router:last_update", time.time())
    set_value("router:status", "READY")
    r2 = send_market_order(sym, "BUY", ALLOWED_VOLUME)
    print(f"  Expect BLOCKED | got status={r2.get('status')} | {r2.get('reason')}")
    print_snapshot("After scenario 2")

    # --- 3) Kill switch ---
    print_snapshot("3) BLOCKED: kill_switch:active")
    approve_router_for(sym)
    set_value("kill_switch:active", True)
    r3 = send_market_order(sym, "SELL", ALLOWED_VOLUME)
    print(f"  Expect BLOCKED | got status={r3.get('status')} | {r3.get('reason')}")
    print_snapshot("After scenario 3")

    # --- 4) Unhealthy heartbeat ---
    print_snapshot("4) BLOCKED: heartbeat unhealthy")
    approve_router_for(sym)
    set_value("kill_switch:active", False)
    set_value("heartbeat:overall", "UNHEALTHY")
    r4 = send_market_order(sym, "BUY", ALLOWED_VOLUME)
    print(f"  Expect BLOCKED | got status={r4.get('status')} | {r4.get('reason')}")
    print_snapshot("After scenario 4")

    print("")
    print("Done. Inspect execution:* in Redis and the BROKER BRIDGE STATUS dashboard section.")


if __name__ == "__main__":
    main()
