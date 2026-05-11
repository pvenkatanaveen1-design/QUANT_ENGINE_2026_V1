"""Smoke/demo runner for the funded drawdown shield (risk/shield.py).

This script does NOT start the infinite daemon loop by default.
It pushes simulated balance/equity into Redis and runs one shield evaluation per scenario.

Continuous shield monitoring:
    python -m risk.shield
"""

# Running `python scripts/test_shield.py` puts `scripts/` on sys.path first - add project root.
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Small pause between scenarios so log lines stay readable in the terminal.
import time

# Redis reads/writes go through the same bus layer the shield uses.
from core.bus import get_value, set_value

# One shield cycle (same function run_shield() calls each iteration).
from core.system_mode import get_system_mode
from risk.shield import publish_risk_status


def print_banner(title: str) -> None:
    """Friendly separator so beginners see scenario boundaries."""
    print("")
    print("==============================================")
    print(title)
    print("==============================================")


def push_account_snapshot(balance: float, equity: float) -> None:
    """
    Simulate broker/account snapshots stored by another component.

    The shield reads these Redis keys every evaluation cycle.
    """
    set_value("account:balance", balance)
    set_value("account:equity", equity)


def print_risk_snapshot(prefix: str) -> None:
    """Pull risk:* keys back from Redis so you can verify publishing succeeded."""
    daily_dd = get_value("risk:daily_dd")
    max_dd = get_value("risk:max_dd")
    shield = get_value("risk:shield")
    status = get_value("risk:status")
    block = get_value("risk:block_trading")
    ts = get_value("risk:last_update")

    print(f"{prefix}")
    print(f"  risk:daily_dd      = {daily_dd}")
    print(f"  risk:max_dd        = {max_dd}")
    print(f"  risk:shield        = {shield}")
    print(f"  risk:status        = {status}")
    print(f"  risk:block_trading = {block}")
    print(f"  risk:last_update   = {ts}")


def run_shield_demo() -> None:
    """
    Walk SAFE -> WARNING -> BLOCKED using controlled equity drops.

    How peaks behave here:
    - First publish at equity 100_000 sets both daily + session peaks at 100_000.
    - Later publishes lower equity without raising equity again, so DD% rises.

    Threshold reminders (see risk/shield.py constants):
    - WARNING when daily DD >= 3% (same peak formula feeds daily DD here).
    - BLOCKED when daily DD >= 5% OR max DD >= 10% (daily breaches first in this demo).
    """

    print_banner("QUANT_ENGINE_2026 | Shield Demo | scripts/test_shield.py")
    print("[INFO] Requires Redis (same REDIS_HOST/REDIS_PORT as core.bus).")
    print("[INFO] Running shield evaluations via publish_risk_status() (one cycle each step).")
    print("[INFO] For the live 5-second loop use: python -m risk.shield")

    if get_system_mode() == "LIVE":
        print("")
        print("[WARN] LIVE MODE DETECTED")
        print("       test_shield.py should not use fake values.")
        print("       Demo continues for infrastructure validation only.")
        print("")

    # ---------------------------------------------------------------------
    # Scenario A - SAFE: equity sits at the intraday peak (no drawdown yet).
    # ---------------------------------------------------------------------
    print_banner("Scenario 1 - SAFE (0% drawdown from peak)")
    print("[STEP] Publishing account:balance / account:equity -> peak establishes at 100,000")
    push_account_snapshot(balance=100_000.0, equity=100_000.0)
    publish_risk_status()
    print_risk_snapshot("[EXPECT] SAFE - DD ~ 0%, block_trading false")

    time.sleep(0.5)

    # ---------------------------------------------------------------------
    # Scenario B - WARNING: 3% drawdown from the peak (at threshold edge).
    # ---------------------------------------------------------------------
    print_banner("Scenario 2 - WARNING (~3% drawdown)")
    print("[STEP] Lower equity to 97,000 -> ~3% from peak -> crosses WARNING gate")
    push_account_snapshot(balance=100_000.0, equity=97_000.0)
    publish_risk_status()
    print_risk_snapshot("[EXPECT] WARNING - elevate attention before critical breach")

    time.sleep(0.5)

    # ---------------------------------------------------------------------
    # Scenario C - BLOCKED: 6% drawdown surpasses daily critical (5%).
    # ---------------------------------------------------------------------
    print_banner("Scenario 3 - BLOCKED (~6% drawdown)")
    print("[STEP] Lower equity to 94,000 -> above daily critical -> BLOCKED + halt flag")
    push_account_snapshot(balance=100_000.0, equity=94_000.0)
    publish_risk_status()
    print_risk_snapshot("[EXPECT] BLOCKED - risk:block_trading should be True")

    print_banner("DONE")
    print("[SUCCESS] Demo finished. Inspect Redis keys above or use your dashboard once wired.")
    print("[NEXT] Continuous shield runner:")
    print("       python -m risk.shield")


if __name__ == "__main__":
    run_shield_demo()
