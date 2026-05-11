"""
Order tracker smoke tests — empty snapshot + optional live DEMO positions.

From project root:

    python scripts/test_order_tracker.py
"""

from __future__ import annotations

from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.bus import get_value
from execution.order_tracker import (
    build_position_summary,
    calculate_total_floating_pnl,
    ensure_demo_account,
    fetch_open_positions,
    initialize_mt5,
    publish_position_updates,
)


def print_summary(title: str, summary: dict) -> None:
    print("")
    print("==============================================")
    print(title)
    print("==============================================")
    print(f"  active_count        = {summary.get('active_count')}")
    print(f"  total_floating_pnl  = {summary.get('total_floating_pnl')}")
    print(f"  winning / losing    = {summary.get('winning_trades')} / {summary.get('losing_trades')}")
    print(f"  tracker_status      = {summary.get('tracker_status')}")
    n = len(summary.get("positions") or [])
    print(f"  positions rows      = {n}")
    for p in summary.get("positions") or []:
        print(
            f"    ticket={p.get('ticket')} {p.get('symbol')} {p.get('side')} "
            f"vol={p.get('volume')} entry={p.get('entry_price')} "
            f"now={p.get('current_price')} pnl={p.get('floating_profit')}"
        )


def print_redis_headline() -> None:
    print("")
    print("Redis headline (orders:*):")
    print(f"  orders:active_count = {get_value('orders:active_count')}")
    print(f"  orders:last_symbol  = {get_value('orders:last_symbol')}")
    print(f"  orders:last_ticket  = {get_value('orders:last_ticket')}")
    print(f"  orders:last_status  = {get_value('orders:last_status')}")
    print(f"  orders:last_profit  = {get_value('orders:last_profit')}")


def main() -> None:
    print("QUANT_ENGINE_2026 | execution.order_tracker | scripts/test_order_tracker.py")

    # --- Simulated flat account (no MT5 required for this block) ---
    empty = build_position_summary([])
    assert calculate_total_floating_pnl(empty["positions"]) == 0.0
    publish_position_updates(empty)
    print_summary("A) Empty / NO_POSITIONS (simulated)", empty)
    print_redis_headline()

    # --- Live DEMO positions (optional) ---
    if not initialize_mt5():
        print("")
        print("B) MT5 not available — skip live fetch (empty snapshot already published).")
        return

    ok, reason = ensure_demo_account()
    if not ok:
        print("")
        print(f"B) Demo check failed — skip live fetch: {reason}")
        return

    live = fetch_open_positions()
    print("")
    print(f"B) Live positions from MT5: count={len(live)}")
    summary = build_position_summary(live)
    publish_position_updates(summary)
    print_summary("B) After live publish", summary)
    print_redis_headline()

    ps = get_value("positions:summary")
    if isinstance(ps, dict):
        print("")
        print("positions:summary keys:", sorted(ps.keys()))


if __name__ == "__main__":
    main()
