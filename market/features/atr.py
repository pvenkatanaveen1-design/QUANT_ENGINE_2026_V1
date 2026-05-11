"""
Live M5 ATR feature engine (Phase 16).

Computes Wilder-style ATR(14) on the last 100 candles per symbol, classifies volatility
(LOW / NORMAL / HIGH) vs configurable price thresholds, and publishes to Redis.

No strategies, signals, or ML — volatility intelligence only.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd

from core.bus import set_value
from core.config_helpers import build_all_config
from core.logger import get_logger

log = get_logger()

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None  # type: ignore[assignment]

ATR_SUPPORTED_SYMBOLS: tuple[str, ...] = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD")
ATR_PERIOD = 14
ATR_CANDLE_COUNT = 100
ATR_ENGINE_INTERVAL_SECONDS = 5.0

VOL_LOW = "LOW"
VOL_NORMAL = "NORMAL"
VOL_HIGH = "HIGH"


def initialize_mt5() -> bool:
    """Reuse an existing MT5 session or connect via `core.pulse.initialize_mt5`."""
    if mt5 is None:
        log.error("atr_engine | MetaTrader5 package not installed")
        return False
    try:
        if mt5.terminal_info() is not None:
            log.debug("atr_engine | MT5 session active — reusing")
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("atr_engine | terminal_info | {}", exc)

    from core.pulse import initialize_mt5 as pulse_initialize_mt5  # noqa: PLC0415

    return bool(pulse_initialize_mt5())


def fetch_candle_data(
    symbol: str,
    timeframe: int | None = None,
    count: int | None = None,
) -> pd.DataFrame:
    """
    Fetch recent OHLCV bars from MT5 into a pandas DataFrame.

    Uses ``TIMEFRAME_M5`` and ``ATR_CANDLE_COUNT`` by default.
    """
    if mt5 is None:
        return pd.DataFrame()
    tf = timeframe if timeframe is not None else int(getattr(mt5, "TIMEFRAME_M5", 5))
    n = int(count) if count is not None else ATR_CANDLE_COUNT

    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            log.warning("atr_engine | unknown symbol | {}", symbol)
            return pd.DataFrame()
        if not info.visible:
            mt5.symbol_select(symbol, True)
    except Exception as exc:  # noqa: BLE001
        log.warning("atr_engine | symbol_select failed | {} | {}", symbol, exc)

    try:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, n)
    except Exception as exc:  # noqa: BLE001
        log.warning("atr_engine | copy_rates failed | {} | {}", symbol, exc)
        return pd.DataFrame()

    if rates is None or len(rates) == 0:
        log.warning("atr_engine | empty rates | symbol={}", symbol)
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


def calculate_true_range(df: pd.DataFrame) -> pd.Series:
    """True range series (needs high, low, close)."""
    if df.empty or not {"high", "low", "close"}.issubset(df.columns):
        return pd.Series(dtype=float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    tr.iloc[0] = float(high.iloc[0] - low.iloc[0])
    return tr


def calculate_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float | None:
    """
    Wilder-smoothed ATR using the last ``period`` TR samples for the seed, then recursion.

    Returns the latest ATR (last bar) or None if not enough data.
    """
    tr = calculate_true_range(df)
    if tr.empty or len(tr) < period:
        return None
    atr = float(np.mean(tr.iloc[:period].values))
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + float(tr.iloc[i])) / period
    return atr


def _thresholds_for_symbol(symbol: str, cfg: dict) -> tuple[float, float]:
    s = symbol.upper()
    if s in ("EURUSD", "GBPUSD"):
        return float(cfg["ATR_VOL_LOW_MAJOR"]), float(cfg["ATR_VOL_HIGH_MAJOR"])
    if s == "USDJPY":
        return float(cfg["ATR_VOL_LOW_JPY"]), float(cfg["ATR_VOL_HIGH_JPY"])
    if s == "XAUUSD":
        return float(cfg["ATR_VOL_LOW_XAU"]), float(cfg["ATR_VOL_HIGH_XAU"])
    if s == "BTCUSD":
        return float(cfg["ATR_VOL_LOW_BTC"]), float(cfg["ATR_VOL_HIGH_BTC"])
    return float(cfg["ATR_VOL_LOW_MAJOR"]), float(cfg["ATR_VOL_HIGH_MAJOR"])


def classify_volatility(atr_value: float, symbol: str, cfg: dict | None = None) -> str:
    """
    Classify ATR vs configured low/high bands for the symbol family.

    - ``ATR < low`` → LOW
    - ``ATR > high`` → HIGH
    - else NORMAL
    """
    if cfg is None:
        cfg = build_all_config()
    low_b, high_b = _thresholds_for_symbol(symbol, cfg)
    if atr_value < low_b:
        return VOL_LOW
    if atr_value > high_b:
        return VOL_HIGH
    return VOL_NORMAL


def publish_atr_status(rows: list[dict[str, Any]], *, last_update: float) -> None:
    """
    Write ``features:{symbol}:*`` for each row and ``features:atr:last_update`` once.

    Each row should include: symbol, atr (float | None), volatility_state, atr_status.
    """
    for row in rows:
        sym = str(row["symbol"])
        set_value(f"features:{sym}:atr", row.get("atr"))
        set_value(f"features:{sym}:volatility_state", row.get("volatility_state"))
        set_value(f"features:{sym}:atr_status", row.get("atr_status"))
    set_value("features:atr:last_update", last_update)
    log.info("atr_engine | published | symbols={} | ts={}", len(rows), last_update)


def _emit_error_snapshot() -> None:
    rows = [
        {
            "symbol": s,
            "atr": None,
            "volatility_state": VOL_NORMAL,
            "atr_status": "ERROR",
        }
        for s in ATR_SUPPORTED_SYMBOLS
    ]
    try:
        publish_atr_status(rows, last_update=time.time())
    except Exception as exc:  # noqa: BLE001
        log.error("atr_engine | error snapshot publish failed | {}", exc)


def _emit_no_mt5_snapshot(status: str) -> None:
    rows = [
        {
            "symbol": s,
            "atr": None,
            "volatility_state": VOL_NORMAL,
            "atr_status": status,
        }
        for s in ATR_SUPPORTED_SYMBOLS
    ]
    try:
        publish_atr_status(rows, last_update=time.time())
    except Exception as exc:  # noqa: BLE001
        log.error("atr_engine | no-mt5 snapshot publish failed | {}", exc)


def run_atr_engine() -> None:
    """Background loop: refresh ATR + volatility labels for all supported symbols."""
    log.info(
        "atr_engine | daemon start | tf=M5 | period={} | candles={} | interval_s={}",
        ATR_PERIOD,
        ATR_CANDLE_COUNT,
        ATR_ENGINE_INTERVAL_SECONDS,
    )
    if mt5 is None:
        while True:
            _emit_no_mt5_snapshot("NO_MT5")
            time.sleep(10.0)

    while True:
        try:
            if not initialize_mt5():
                _emit_no_mt5_snapshot("NO_MT5")
                time.sleep(5.0)
                continue

            cfg = build_all_config()
            now = time.time()
            rows: list[dict[str, Any]] = []

            for symbol in ATR_SUPPORTED_SYMBOLS:
                df = fetch_candle_data(symbol)
                if df.empty or len(df) < ATR_PERIOD + 1:
                    rows.append(
                        {
                            "symbol": symbol,
                            "atr": None,
                            "volatility_state": VOL_NORMAL,
                            "atr_status": "NO_DATA",
                        }
                    )
                    continue
                atr_val = calculate_atr(df, ATR_PERIOD)
                if atr_val is None:
                    rows.append(
                        {
                            "symbol": symbol,
                            "atr": None,
                            "volatility_state": VOL_NORMAL,
                            "atr_status": "NO_DATA",
                        }
                    )
                    continue
                vol = classify_volatility(float(atr_val), symbol, cfg)
                rows.append(
                    {
                        "symbol": symbol,
                        "atr": round(float(atr_val), 8),
                        "volatility_state": vol,
                        "atr_status": "RUNNING",
                    }
                )

            publish_atr_status(rows, last_update=now)
        except Exception:  # noqa: BLE001
            log.exception("atr_engine | tick failed")
            _emit_error_snapshot()

        time.sleep(ATR_ENGINE_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_atr_engine()


# ─── STANDALONE HELPERS (used by strategies and regime_detector) ──────────────
# These functions work with raw price lists — no Redis, no MT5, no pandas required.
# Used by: strategies/alpha_breakout.py, systems/intelligence/regime_detector.py
# Also used by: systems/research/backtester.py during simulation.

def calculate_atr_from_candles(
    highs:  list,
    lows:   list,
    closes: list,
    period: int = 14,
) -> float:
    """
    Calculate ATR(period) from raw price lists.  Returns float or 0.0 on failure.
    Accepts both plain lists and numpy arrays.

    This is a convenience wrapper that strategies can call without importing
    pandas or MT5.  All strategy logic uses this function for ATR-based SL sizing.

    Example:
        from market.features.atr import calculate_atr_from_candles
        atr = calculate_atr_from_candles(df["high"].tolist(), df["low"].tolist(), df["close"].tolist())
        sl  = atr * 1.5  # SL = 1.5 × ATR
    """
    try:
        h = [float(x) for x in highs]
        l = [float(x) for x in lows]
        c = [float(x) for x in closes]

        if len(c) < period + 1:
            return 0.0

        trs = []
        for i in range(1, len(c)):
            hl  = h[i] - l[i]
            hpc = abs(h[i] - c[i - 1])
            lpc = abs(l[i] - c[i - 1])
            trs.append(max(hl, hpc, lpc))

        if len(trs) < period:
            return 0.0

        # Wilder smoothing
        atr_val = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr_val = (atr_val * (period - 1) + tr) / period
        return round(atr_val, 4)
    except Exception:
        return 0.0
