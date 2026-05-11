"""
Trade manager — simulated management decision smoke tests.

From QUANT_ENGINE_2026 root:

    python scripts/test_trade_manager.py

Uses synthetic positions (no MT5 required for the main scenarios). Optionally touches Redis
if available (publish calls).
"""

from __future__ import annotations

from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from execution.trade_manager import (
    ACTION_HOLD,
    ACTION_MOVE_SL_BREAKEVEN,
    ACTION_PARTIAL_CLOSE,
    ACTION_TRAIL_STOP,
    evaluate_trade_management,
    publish_trade_management_status,
)


R = 10.0


def _base_row(ticket: int, symbol: str, pnl: float) -> dict:
    return {
        "ticket": ticket,
        "symbol": symbol,
        "side": "BUY",
        "volume": 0.1,
        "entry_price": 1.0,
        "current_price": 1.0,
        "sl": 0.0,
        "tp": 0.0,
        "floating_profit": pnl,
        "open_time": 0,
    }


def print_decision(title: str, d: dict) -> None:
    print("")
    print("==============================================")
    print(title)
    print("==============================================")
    print(f"  ticket           = {d.get('ticket')}")
    print(f"  symbol           = {d.get('symbol')}")
    print(f"  action           = {d.get('action')}")
    print(f"  reason           = {d.get('reason')}")
    print(f"  floating_profit  = {d.get('floating_profit')}")
    print(f"  r_unit           = {d.get('r_unit')}")


def main() -> None:
    print("QUANT_ENGINE_2026 | execution.trade_manager | scripts/test_trade_manager.py")
    print(f"Using synthetic R_unit = {R} (override with TRADE_MANAGER_R_UNIT in .env when integrated).")

    # Losing trade → HOLD (no tier)
    d_loss = evaluate_trade_management([_base_row(101, "EURUSD", -25.0)], r_unit=R)
    print_decision("1) Losing trade (pnl -25) -> expect HOLD_POSITION", d_loss)
    assert d_loss.get("action") == ACTION_HOLD

    # Small profit < 1R → HOLD
    d_small = evaluate_trade_management([_base_row(102, "EURUSD", 5.0)], r_unit=R)
    print_decision("2) Small winner (pnl 5 < 1R) -> expect HOLD_POSITION", d_small)
    assert d_small.get("action") == ACTION_HOLD

    # > 1R breakeven trigger
    d_be = evaluate_trade_management([_base_row(103, "GBPUSD", 15.0)], r_unit=R)
    print_decision("3) pnl 15 > 1R -> expect MOVE_SL_TO_BREAKEVEN", d_be)
    assert d_be.get("action") == ACTION_MOVE_SL_BREAKEVEN

    # > 2R trail trigger
    d_tr = evaluate_trade_management([_base_row(104, "XAUUSD", 25.0)], r_unit=R)
    print_decision("4) pnl 25 > 2R -> expect TRAIL_STOP", d_tr)
    assert d_tr.get("action") == ACTION_TRAIL_STOP

    # > 3R partial trigger
    d_pc = evaluate_trade_management([_base_row(105, "BTCUSD", 35.0)], r_unit=R)
    print_decision("5) pnl 35 > 3R -> expect PARTIAL_CLOSE", d_pc)
    assert d_pc.get("action") == ACTION_PARTIAL_CLOSE

    # Empty list
    d_empty = evaluate_trade_management([], r_unit=R)
    print_decision("6) No positions -> HOLD (NO_OPEN_POSITIONS)", d_empty)
    assert d_empty.get("action") == ACTION_HOLD
    assert "NO_OPEN" in str(d_empty.get("reason") or "")

    # Lead position = highest ticket with a weaker row present
    multi = [
        _base_row(200, "EURUSD", 5.0),
        _base_row(300, "EURUSD", 35.0),
    ]
    d_lead = evaluate_trade_management(multi, r_unit=R)
    print_decision("7) Multiple rows -> lead ticket 300, PARTIAL_CLOSE", d_lead)
    assert d_lead.get("ticket") == 300
    assert d_lead.get("action") == ACTION_PARTIAL_CLOSE

    # Push last scenario to Redis (best effort)
    try:
        publish_trade_management_status(d_lead, manager_status="RUNNING")
        print("")
        print("Redis: published trade_manager:* snapshot from scenario 7 (if Redis up).")
    except Exception as exc:  # noqa: BLE001
        print("")
        print(f"Redis publish skipped: {exc}")

    print("")
    print("All scripted assertions passed.")


if __name__ == "__main__":
    main()
