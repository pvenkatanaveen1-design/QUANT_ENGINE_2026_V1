"""
Simulated trade requests through `execution/router.py`.

Requires Redis. From project root:

    python scripts/test_router.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.bus import get_value, set_value
from execution.router import evaluate_trade_request
from risk.cost_guard import evaluate_cost_guard_once
from risk.news_guard import evaluate_news_guard_once


def push_market(symbol: str, bid: float, ask: float) -> None:
    sym = str(symbol).upper()
    spr = abs(float(ask) - float(bid))
    set_value(f"market:{sym}:bid", bid)
    set_value(f"market:{sym}:ask", ask)
    set_value(f"market:{sym}:spread", spr)


def tight_background(except_symbol: str | None = None) -> None:
    pairs = [
        ("EURUSD", 1.08500, 1.08501),
        ("GBPUSD", 1.26500, 1.26501),
        ("USDJPY", 150.000, 150.010),
        ("XAUUSD", 2650.00, 2650.01),
        ("BTCUSD", 95000.00, 95005.00),
    ]
    for sym, b, a in pairs:
        if except_symbol and sym == except_symbol.upper():
            continue
        push_market(sym, b, a)


def healthy_core_session_and_shield() -> None:
    """Baseline: heart + shield + kill + clock + mode + news inactive."""
    set_value("heartbeat:overall", "HEALTHY")
    set_value("risk:block_trading", False)
    set_value("kill_switch:active", False)
    set_value("news:blackout", "INACTIVE")
    set_value("news:blackout_reason", "Test baseline - no blackout")
    set_value("clock:session", "London-New York overlap")
    set_value("SYSTEM_MODE", "TEST")


def print_router_snapshot(title: str) -> None:
    print("")
    print("==============================================")
    print(title)
    print("==============================================")
    print(
        f"  router:last_symbol   = {get_value('router:last_symbol')}\n"
        f"  router:last_decision = {get_value('router:last_decision')}\n"
        f"  router:last_reason   = {get_value('router:last_reason')}\n"
        f"  router:last_update   = {get_value('router:last_update')}\n"
        f"  router:status        = {get_value('router:status')}"
    )


def run_case(label: str, symbol: str) -> None:
    r = evaluate_trade_request(symbol)
    print(f"  {label}")
    print(f"    {symbol} -> {r['decision']}")
    print(f"    reason: {r['reason']}")
    if r.get("failed_check"):
        print(f"    failed_check: {r['failed_check']}")


def main() -> None:
    print("QUANT_ENGINE_2026 | execution.router | scripts/test_router.py")

    # --- 1) Healthy path: EURUSD APPROVED ---
    print_router_snapshot("1) Healthy environment -> EURUSD APPROVED")
    healthy_core_session_and_shield()
    tight_background(None)
    push_market("EURUSD", 1.10000, 1.10008)
    evaluate_cost_guard_once()
    run_case("Expect APPROVED (all gates green)", "EURUSD")

    # --- 2) Wide XAU spread -> BLOCKED (cost guard) ---
    print_router_snapshot("2) XAUUSD wide spread -> BLOCKED (cost)")
    healthy_core_session_and_shield()
    tight_background("XAUUSD")
    push_market("XAUUSD", 2650.00, 2654.50)
    evaluate_cost_guard_once()
    run_case("Expect BLOCKED - spread too high for XAUUSD", "XAUUSD")

    # --- 3) News blackout -> BTCUSD BLOCKED (before cost check order) ---
    print_router_snapshot("3) News blackout -> BTCUSD BLOCKED (news)")
    healthy_core_session_and_shield()
    tight_background(None)
    evaluate_cost_guard_once()
    now = datetime.now(timezone.utc)
    evaluate_news_guard_once(
        now_utc=now,
        events=[{"name": "Test CPI (script)", "event_time": now}],
    )
    run_case("Expect BLOCKED - news blackout active", "BTCUSD")

    # --- 4) Kill switch -> EURUSD BLOCKED ---
    print_router_snapshot("4) Kill switch -> EURUSD BLOCKED")
    healthy_core_session_and_shield()
    tight_background(None)
    evaluate_cost_guard_once()
    reset_ts = datetime.now(timezone.utc)
    evaluate_news_guard_once(
        now_utc=reset_ts,
        events=[{"name": "Far future event", "event_time": reset_ts + timedelta(days=30)}],
    )
    set_value("kill_switch:active", True)
    run_case("Expect BLOCKED - kill switch active", "EURUSD")

    print("")
    print("Done. See EXECUTION ROUTER STATUS on the dashboard or inspect router:* in Redis.")


if __name__ == "__main__":
    main()
