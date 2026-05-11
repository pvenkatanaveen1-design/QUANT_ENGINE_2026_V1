"""
MetaTrader 5 candle and account data access.

This module is the only data source now. In future, COT data, FRED yield data,
and yfinance DXY data can be added as separate functions here without touching
other modules.

No Redis — MT5 Python API only.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None  # type: ignore[assignment]

_DEFAULT_SYMBOL = "XAUUSD"
_DEFAULT_BARS = 500


def _symbol() -> str:
    return os.getenv("SYMBOL_DEFAULT", _DEFAULT_SYMBOL).strip().upper()


def is_mt5_connected() -> bool:
    """Return True if MT5 terminal is reachable and initialized."""
    try:
        if mt5 is None:
            return False
        if mt5.terminal_info() is None:
            return False
        return True
    except Exception as exc:
        log.debug("is_mt5_connected: %s", exc)
        return False


def _ensure_mt5() -> None:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 package not installed. pip install MetaTrader5")
    if not is_mt5_connected():
        ok = mt5.initialize()
        if not ok:
            err = mt5.last_error()
            raise RuntimeError(
                f"MT5 not connected. Open MetaTrader 5 and log in to a demo account. last_error={err}"
            )
        if mt5.terminal_info() is None:
            raise RuntimeError("MT5 initialize returned True but terminal_info is None.")


def get_candles(
    symbol: str | None = None,
    timeframe: int | None = None,
    bars: int = _DEFAULT_BARS,
) -> pd.DataFrame:
    """
    Fetch OHLCV from MT5. Default: XAUUSD, M15, 500 bars.

    Returns DataFrame columns: time, open, high, low, close, volume, mid
    """
    try:
        _ensure_mt5()
        sym = (symbol or _symbol()).upper()
        tf = timeframe if timeframe is not None else mt5.TIMEFRAME_M15
        n = int(bars)
        rates = mt5.copy_rates_from_pos(sym, tf, 0, n)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No candle data for {sym} (check Market Watch / symbol name).")
        df = pd.DataFrame(rates)
        if "tick_volume" in df.columns and "volume" not in df.columns:
            df["volume"] = df["tick_volume"]
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df["mid"] = (df["open"].astype(float) + df["close"].astype(float)) / 2.0
        out = df[["time", "open", "high", "low", "close", "volume"]].copy()
        out["mid"] = df["mid"]
        return out
    except RuntimeError:
        raise
    except Exception as exc:
        log.exception("get_candles failed")
        raise RuntimeError(f"get_candles failed: {exc}") from exc


def get_h1_candles(symbol: str | None = None, bars: int = 200) -> pd.DataFrame:
    """H1 candles for regime / structure (default 200 bars)."""
    try:
        _ensure_mt5()
        sym = (symbol or _symbol()).upper()
        return get_candles(sym, mt5.TIMEFRAME_H1, bars)
    except RuntimeError:
        raise
    except Exception as exc:
        log.exception("get_h1_candles failed")
        raise RuntimeError(f"get_h1_candles failed: {exc}") from exc


def get_current_tick(symbol: str | None = None) -> dict[str, Any]:
    """Latest tick for spread / entry prices."""
    try:
        _ensure_mt5()
        sym = (symbol or _symbol()).upper()
        try:
            mt5.symbol_select(sym, True)
        except Exception:
            pass
        t = mt5.symbol_info_tick(sym)
        if t is None:
            raise RuntimeError(f"No tick for {sym}")
        return {
            "symbol": sym,
            "bid": float(t.bid),
            "ask": float(t.ask),
            "time": int(getattr(t, "time", 0) or 0),
            "time_msc": int(getattr(t, "time_msc", 0) or 0),
        }
    except RuntimeError:
        raise
    except Exception as exc:
        log.exception("get_current_tick failed")
        raise RuntimeError(f"get_current_tick failed: {exc}") from exc


def get_account_info() -> dict[str, Any]:
    """Account snapshot from MT5."""
    try:
        _ensure_mt5()
        a = mt5.account_info()
        if a is None:
            raise RuntimeError("mt5.account_info() returned None (not logged in?)")
        return {
            "login": int(a.login),
            "server": str(a.server),
            "balance": float(a.balance),
            "equity": float(a.equity),
            "margin": float(a.margin),
            "currency": str(a.currency),
            "trade_mode": int(a.trade_mode),
            "leverage": int(a.leverage),
        }
    except RuntimeError:
        raise
    except Exception as exc:
        log.exception("get_account_info failed")
        raise RuntimeError(f"get_account_info failed: {exc}") from exc
