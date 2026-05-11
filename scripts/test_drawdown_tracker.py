"""Demo runner for `risk/drawdown_tracker.py` — simulates equity path and prints dd:* keys.

Requires Redis. From project root:

    python scripts/test_drawdown_tracker.py
"""

from __future__ import annotations

from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.bus import get_value, set_value
from risk.drawdown_tracker import evaluate_drawdown_once


def push_account_snapshot(balance: float, equity: float) -> None:
    """Mirror live infra: another process would write these keys."""
    set_value("account:balance", balance)
    set_value("account:equity", equity)


def print_dd_tracker(prefix: str) -> None:
    """Readable dump for beginners."""
    print(prefix)
    evaluate_drawdown_once()
    sb = get_value("dd:start_balance")
    peak = get_value("dd:peak_equity")
    low = get_value("dd:lowest_equity")
    cur = get_value("dd:current_daily_dd")
    mx = get_value("dd:max_daily_dd")
    reset = get_value("dd:last_reset")
    status = get_value("dd:tracker_status")

    print(f"  dd:start_balance     = {sb}")
    print(f"  dd:peak_equity       = {peak}")
    print(f"  dd:lowest_equity     = {low}")
    print(f"  dd:current_daily_dd  = {cur}")
    print(f"  dd:max_daily_dd      = {mx}")
    print(f"  dd:tracker_status    = {status}")
    print(f"  dd:last_reset        = {reset}")


def run_demo() -> None:
    """100000 -> 101000 -> 99000 -> 97000 (equity path)."""
    print("==============================================")
    print("QUANT_ENGINE_2026 | Drawdown tracker demo")
    print("==============================================")
    print("[INFO] Simulated path: 100k -> 101k -> 99k -> 97k")
    print("[INFO] Reference at day open: balance (preferred) == equity each step here.")
    print("")

    steps = [
        (100_000.0, 100_000.0, "Step 1 — Day open @ 100000 / 100000"),
        (101_000.0, 101_000.0, "Step 2 — Profit: equity 101000 (peak rises)"),
        (99_000.0, 99_000.0, "Step 3 — Drop to 99000"),
        (97_000.0, 97_000.0, "Step 4 — Drop to 97000 (DD deepens)"),
    ]

    for bal, eq, title in steps:
        print("----------------------------------------------")
        print(title)
        push_account_snapshot(bal, eq)
        print_dd_tracker("Redis snapshot after evaluate_drawdown_once():")

    print("")
    print("Done. Compare `DAILY_DD_WARNING` / `DAILY_DD_BLOCK` in `.env` with dd:tracker_status.")


if __name__ == "__main__":
    run_demo()
