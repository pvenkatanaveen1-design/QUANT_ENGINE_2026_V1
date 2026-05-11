"""
Load M15/H1 history from MetaTrader 5 and run walk-forward regime detection.

**Preferred:** use the Streamlit dashboard — ``streamlit run dashboard_app.py`` →
Operator console → regime backtest (same behaviour).

Does not send orders. Requires MT5 terminal open and symbol visible in Market Watch CLI usage (fallback only):

    python run_regime_backtest.py
    python run_regime_backtest.py --m15-bars 3000 --step 1

Output: prints summary and writes logs/regime_backtest_walk.csv
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.data_feed import get_candles, is_mt5_connected  # noqa: E402
from core.regime_backtest import run_regime_walk_forward, summarize_regime_series  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    logging.info("Tip: prefer Streamlit dashboard → Operator console for regime backtest (UI-first workflow).")
    p = argparse.ArgumentParser(description="Regime walk-forward on MT5 history")
    p.add_argument("--m15-bars", type=int, default=2500, help="M15 bars to fetch from MT5")
    p.add_argument("--h1-bars", type=int, default=2000, help="H1 bars to fetch (should cover M15 span)")
    p.add_argument("--min-m15", type=int, default=100, help="First bar index to start regime detection")
    p.add_argument("--step", type=int, default=1, help="Stride between M15 bars (e.g. 4 = hourly)")
    args = p.parse_args()

    sym = os.getenv("SYMBOL_DEFAULT", "XAUUSD").strip().upper()
    if not is_mt5_connected():
        logging.error("MT5 not connected — open MetaTrader 5 and log in, then retry.")
        raise SystemExit(1)

    try:
        import MetaTrader5 as mt5

        df_m15 = get_candles(sym, mt5.TIMEFRAME_M15, args.m15_bars)
        df_h1 = get_candles(sym, mt5.TIMEFRAME_H1, args.h1_bars)
    except Exception as exc:
        logging.error("Failed to load MT5 candles: %s", exc)
        raise SystemExit(1) from exc

    out_path = _ROOT / "logs" / "regime_backtest_walk.csv"
    df_out = run_regime_walk_forward(
        df_m15,
        df_h1,
        min_m15_bars=args.min_m15,
        step=args.step,
        output_csv=out_path,
    )
    summary = summarize_regime_series(df_out)
    logging.info("Symbol=%s rows=%s summary=%s", sym, len(df_out), summary)
    if len(df_out):
        print(df_out.tail(12).to_string(index=False))


if __name__ == "__main__":
    main()
