"""
Trade journal integration tests — Redis snapshots -> SQLite rows.

From project root:

    python scripts/test_trade_logger.py
"""

from __future__ import annotations

from pathlib import Path
import sys
import time

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.bus import get_value, set_value
from journal.trade_logger import (
    _count_trades,
    fetch_recent_trades,
    initialize_database,
    reset_dedup_cursor,
    tick_trade_logger,
)


def push_execution_snapshot(
    *,
    symbol: str,
    side: str,
    volume: float,
    status: str,
    ticket: int | None,
    reason: str,
    ts: float | None = None,
) -> float:
    """Write broker-style execution:* keys; returns timestamp used."""
    if ts is None:
        ts = time.time()
    sym = str(symbol).upper()
    set_value("execution:last_symbol", sym)
    set_value("execution:last_side", str(side).upper())
    set_value("execution:last_volume", float(volume))
    set_value("execution:last_status", str(status).upper())
    set_value("execution:last_ticket", ticket)
    set_value("execution:last_reason", reason)
    set_value("execution:last_update", ts)
    return float(ts)


def seed_context(*, router: str = "APPROVED", session: str = "London-New York overlap") -> None:
    set_value("router:last_decision", router)
    set_value("clock:session", session)


def print_recent(title: str) -> None:
    print("")
    print("==============================================")
    print(title)
    print("==============================================")
    rows = fetch_recent_trades(15)
    if not rows:
        print("  (no rows yet)")
        return
    for r in reversed(rows):
        print(
            f"  id={r['id']} | {r['execution_status']:8} | {r['symbol'] or '-':7} | "
            f"{r['side'] or '-':4} | vol={r['volume']} | ticket={r['mt5_ticket']}"
        )


def main() -> None:
    print("QUANT_ENGINE_2026 | journal.trade_logger | scripts/test_trade_logger.py")
    initialize_database()
    seed_context()

    # 1) FILLED
    reset_dedup_cursor()
    push_execution_snapshot(
        symbol="EURUSD",
        side="BUY",
        volume=0.01,
        status="FILLED",
        ticket=900001,
        reason="Demo fill (simulated)",
    )
    assert tick_trade_logger() is True
    print_recent("After FILLED")

    # 2) REJECTED
    push_execution_snapshot(
        symbol="EURUSD",
        side="BUY",
        volume=0.01,
        status="REJECTED",
        ticket=None,
        reason="MT5 retcode test",
        ts=time.time(),
    )
    assert tick_trade_logger() is True
    print_recent("After REJECTED")

    # 3) BLOCKED
    push_execution_snapshot(
        symbol="XAUUSD",
        side="SELL",
        volume=0.01,
        status="BLOCKED",
        ticket=None,
        reason="Kill switch (simulated)",
        ts=time.time(),
    )
    assert tick_trade_logger() is True
    print_recent("After BLOCKED")

    # 4) APPROVED (category for audits / future paths)
    push_execution_snapshot(
        symbol="GBPUSD",
        side="BUY",
        volume=0.01,
        status="APPROVED",
        ticket=None,
        reason="Pre-send approval snapshot (simulated)",
        ts=time.time(),
    )
    assert tick_trade_logger() is True
    print_recent("After APPROVED")

    # 5) Duplicate fingerprint — should NOT insert twice
    before = _count_trades()
    assert tick_trade_logger() is False
    after = _count_trades()
    print("")
    print(f"  Duplicate tick guard: count before={before} after={after} (expect equal)")
    assert before == after

    print("")
    print("  journal:total_trades =", get_value("journal:total_trades"))
    print("  journal:last_status  =", get_value("journal:last_status"))
    print("Done.")


if __name__ == "__main__":
    main()
