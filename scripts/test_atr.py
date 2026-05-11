"""
ATR feature engine tests — M5 ATR + volatility states for all supported symbols.

From QUANT_ENGINE_2026 root:

    python scripts/test_atr.py

Requires MT5 + symbols available in the terminal for the live block.
Always runs a **synthetic** OHLC sanity check (no MT5).
"""

from __future__ import annotations

from pathlib import Path
import sys
import time

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from market.features.atr import (
    ATR_CANDLE_COUNT,
    ATR_PERIOD,
    ATR_SUPPORTED_SYMBOLS,
    calculate_atr,
    calculate_true_range,
    classify_volatility,
    fetch_candle_data,
    initialize_mt5,
    publish_atr_status,
)


def _synthetic_ohlc() -> pd.DataFrame:
    """Simple rising series so TR and ATR are well defined."""
    n = 40
    base = [1.1000 + i * 0.0005 for i in range(n)]
    rows = []
    for i in range(n):
        o_ = base[i]
        c = base[i] + 0.0002
        h = max(o_, c) + 0.0005
        l = min(o_, c) - 0.0003
        rows.append({"open": o_, "high": h, "low": l, "close": c})
    return pd.DataFrame(rows)


def print_banner(title: str) -> None:
    print("")
    print("==============================================")
    print(title)
    print("==============================================")


def main() -> None:
    print("QUANT_ENGINE_2026 | market.features.atr | scripts/test_atr.py")

    df_syn = _synthetic_ohlc()
    tr = calculate_true_range(df_syn)
    atr_syn = calculate_atr(df_syn, ATR_PERIOD)
    print_banner("A) Synthetic OHLC (no MT5)")
    print(f"  rows={len(df_syn)} | TR length={len(tr)} | ATR(14)={atr_syn}")
    assert atr_syn is not None and atr_syn > 0, "synthetic ATR should be positive"

    if not initialize_mt5():
        print("")
        print("B) MT5 not available — skip live symbols (synthetic check passed).")
        return

    print_banner(f"B) Live symbols | M5 | last {ATR_CANDLE_COUNT} candles | period={ATR_PERIOD}")
    rows_out = []
    for symbol in ATR_SUPPORTED_SYMBOLS:
        df = fetch_candle_data(symbol)
        if df.empty or len(df) < ATR_PERIOD + 1:
            print(f"  {symbol}: NO_DATA (rows={len(df)})")
            rows_out.append(
                {
                    "symbol": symbol,
                    "atr": None,
                    "volatility_state": "NORMAL",
                    "atr_status": "NO_DATA",
                }
            )
            continue
        atr_val = calculate_atr(df, ATR_PERIOD)
        if atr_val is None:
            print(f"  {symbol}: NO_DATA (ATR None)")
            rows_out.append(
                {
                    "symbol": symbol,
                    "atr": None,
                    "volatility_state": "NORMAL",
                    "atr_status": "NO_DATA",
                }
            )
            continue
        vol = classify_volatility(float(atr_val), symbol)
        print(f"  {symbol}: ATR={atr_val:.8f} | volatility={vol} | status=RUNNING")
        rows_out.append(
            {
                "symbol": symbol,
                "atr": round(float(atr_val), 8),
                "volatility_state": vol,
                "atr_status": "RUNNING",
            }
        )

    ts = time.time()
    publish_atr_status(rows_out, last_update=ts)
    print("")
    print(f"Redis: published batch | features:atr:last_update={ts}")


if __name__ == "__main__":
    main()
