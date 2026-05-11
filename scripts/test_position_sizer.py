"""Exercise `risk/position_sizer.py` with printed scenarios + Redis publish."""

# Running by path puts `scripts/` first on sys.path - prepend project root for `core` imports.
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from risk.position_sizer import (
    calculate_lot_size,
    calculate_risk_amount,
    get_pip_value,
    publish_position_size,
)


def print_scenario(title, symbol, balance, risk_pct, sl_pips):
    """Pretty terminal output so beginners can audit every intermediate value."""
    print("")
    print("==============================================")
    print(title)
    print("==============================================")
    print(f"  Symbol        : {symbol}")
    print(f"  Balance       : {balance:,.2f}")
    print(f"  Risk %        : {risk_pct}%")
    print(f"  Stop-loss pips: {sl_pips}")

    pip_v = get_pip_value(symbol)
    risk_amt = calculate_risk_amount(balance, risk_pct)
    lot, detail = calculate_lot_size(balance, risk_pct, sl_pips, symbol)

    print(f"  Pip value ($/pip/lot): {pip_v}")
    print(f"  Risk amount ($)       : {risk_amt}")
    if lot is not None:
        denom = float(sl_pips) * float(pip_v)
        print(f"  Denominator (SL*pip)  : {denom}")
        print(f"  Raw lot               : {detail.get('lot_size_raw')}")
        print(f"  Rounded lot           : {lot}")
    else:
        print(f"  ERROR                 : {detail.get('error')}")

    publish_position_size(symbol, risk_pct, sl_pips, balance=balance)
    print("  [Redis] sizing:* keys updated (see sizing:last_* ).")


def run_position_sizer_demo():
    """Three publisher-required examples."""
    print_banner()
    print("[INFO] Requires Redis for publish_position_size.")
    print("[INFO] Run from QUANT_ENGINE_2026 or rely on sys.path fix above.")
    print("")

    print_scenario(
        "Example 1 - EURUSD",
        "EURUSD",
        balance=100_000,
        risk_pct=0.5,
        sl_pips=20,
    )
    print_scenario(
        "Example 2 - XAUUSD",
        "XAUUSD",
        balance=100_000,
        risk_pct=1.0,
        sl_pips=50,
    )
    print_scenario(
        "Example 3 - BTCUSD",
        "BTCUSD",
        balance=50_000,
        risk_pct=0.25,
        sl_pips=100,
    )

    print("")
    print("[DONE] Final Redis snapshot reflects Example 3 (last publish wins).")


def print_banner():
    print("==============================================")
    print("QUANT_ENGINE_2026 | Position sizer tests")
    print("==============================================")


if __name__ == "__main__":
    run_position_sizer_demo()
