"""
Signal generation from M15/H1 data, regime, and strategy allowlist.

No Redis. Delivery targets computed from candle history (IST session logic).

Implements sweep / OB heuristics per project spec (simplified but structured).
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pandas_ta as ta

from core.strategy_map import get_strategy_params

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def pip_size(symbol: str) -> float:
    u = str(symbol).upper()
    if u.startswith("XAU") or u.startswith("XAG"):
        return 0.01
    if "JPY" in u:
        return 0.01
    if u.startswith("BTC"):
        return 1.0
    return 0.0001


def compute_delivery_targets(df_m15: pd.DataFrame, df_h1: pd.DataFrame, symbol: str) -> dict[str, Any]:
    """Previous day/week highs/lows, Asian range IST, round numbers, current mid."""
    try:
        m = df_m15.copy()
        m["time"] = pd.to_datetime(m["time"], utc=True)
        m["ist"] = m["time"].dt.tz_convert(IST)
        m["date_ist"] = m["ist"].dt.date

        dates = sorted(m["date_ist"].unique())
        today_ist = dates[-1]
        prev_day = dates[-2] if len(dates) > 1 else today_ist
        m_prev = m[m["date_ist"] == prev_day]
        previous_day_high = float(m_prev["high"].max()) if len(m_prev) else float(m["high"].iloc[-1])
        previous_day_low = float(m_prev["low"].min()) if len(m_prev) else float(m["low"].iloc[-1])

        last_5_days = dates[-5:] if len(dates) >= 5 else dates
        mw = m[m["date_ist"].isin(last_5_days)]
        previous_week_high = float(mw["high"].max())
        previous_week_low = float(mw["low"].min())

        def in_asian(ts: pd.Timestamp) -> bool:
            t = ts.time()
            return (t >= dtime(22, 0)) or (t <= dtime(1, 30))

        ma = m[m["ist"].apply(lambda x: in_asian(x))]
        asian_high = float(ma["high"].max()) if len(ma) else float(m["high"].tail(32).max())
        asian_low = float(ma["low"].min()) if len(ma) else float(m["low"].tail(32).min())

        last = m.iloc[-1]
        mid = float(last["close"])

        p = pip_size(symbol)
        rn50 = round(mid / (50 * p)) * (50 * p)
        rn100 = round(mid / (100 * p)) * (100 * p)

        return {
            "previous_day_high": previous_day_high,
            "previous_day_low": previous_day_low,
            "previous_week_high": previous_week_high,
            "previous_week_low": previous_week_low,
            "asian_high": asian_high,
            "asian_low": asian_low,
            "nearest_round_50": float(rn50),
            "nearest_round_100": float(rn100),
            "current_mid": mid,
        }
    except Exception as exc:
        log.exception("compute_delivery_targets: %s", exc)
        last = float(df_m15["close"].iloc[-1])
        return {
            "previous_day_high": last,
            "previous_day_low": last,
            "previous_week_high": last,
            "previous_week_low": last,
            "asian_high": last,
            "asian_low": last,
            "nearest_round_50": last,
            "nearest_round_100": last,
            "current_mid": last,
        }


def _nearest_target_above(price: float, targets: list[float]) -> float:
    above = [t for t in targets if t > price + 1e-8]
    return min(above) if above else price


def _nearest_target_below(price: float, targets: list[float]) -> float:
    below = [t for t in targets if t < price - 1e-8]
    return max(below) if below else price


def _try_manipulation(
    df_m15: pd.DataFrame, params: dict, delivery: dict, symbol: str
) -> dict[str, Any] | None:
    if len(df_m15) < 8:
        return None
    tail = df_m15.tail(6).copy()
    vol_ma = float(df_m15["volume"].astype(float).tail(20).mean())
    hi_as = float(delivery["asian_high"])
    lo_as = float(delivery["asian_low"])
    pip = pip_size(symbol)
    mn = float(params.get("sweep_min_pips", 3)) * pip
    mx = float(params.get("sweep_max_pips", 20)) * pip

    for i in range(-5, 0):
        row = tail.iloc[i]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        v = float(row["volume"])
        # sweep above asian high, close back inside
        if h > hi_as + mn and c < hi_as and h - hi_as <= mx:
            if v < vol_ma * 1.5:
                continue
            nxt = tail.iloc[i + 1]
            if float(nxt["close"]) < c:
                entry = float(nxt["open"])
                sl = h + 3 * pip
                tp = _nearest_target_below(entry, [delivery["previous_day_low"], lo_as, delivery["nearest_round_50"]])
                return {
                    "direction": "sell",
                    "entry_price": entry,
                    "sl": sl,
                    "tp1": tp,
                    "tp2": tp,
                    "tp3": tp,
                    "delivery_target": tp,
                    "reason": "Manipulation sweep above Asian high, reversal",
                }
        if l < lo_as - mn and c > lo_as and lo_as - l <= mx:
            if v < vol_ma * 1.5:
                continue
            nxt = tail.iloc[i + 1]
            if float(nxt["close"]) > c:
                entry = float(nxt["open"])
                sl = l - 3 * pip
                tp = _nearest_target_above(entry, [delivery["previous_day_high"], hi_as, delivery["nearest_round_50"]])
                return {
                    "direction": "buy",
                    "entry_price": entry,
                    "sl": sl,
                    "tp1": tp,
                    "tp2": tp,
                    "tp3": tp,
                    "delivery_target": tp,
                    "reason": "Manipulation sweep below Asian low, reversal",
                }
    return None


def _try_ob(df_h1: pd.DataFrame, params: dict, delivery: dict, direction: str, symbol: str) -> dict[str, Any] | None:
    """Order-block style retest (simplified fresh OB heuristic)."""
    try:
        n = int(params.get("ob_lookback_candles", 20))
        if len(df_h1) < n + 4:
            return None
        seg = df_h1.tail(n).reset_index(drop=True)
        o = seg["open"].astype(float)
        c = seg["close"].astype(float)
        h = seg["high"].astype(float)
        l = seg["low"].astype(float)
        pip = pip_size(symbol)
        # Last bearish candle before strongest up close in window (long bias)
        rel_idx = int((c - o).values.argmax())
        if rel_idx < 2 or direction != "buy":
            return None
        ob_i = rel_idx - 1
        if c.iloc[ob_i] >= o.iloc[ob_i]:
            return None
        ob_high, ob_low = float(h.iloc[ob_i]), float(l.iloc[ob_i])
        entry = (ob_high + ob_low) / 2
        sl = ob_low - 3 * pip
        tp = _nearest_target_above(entry, [delivery["asian_high"], delivery["previous_day_high"], delivery["nearest_round_50"]])
        if tp <= entry + pip:
            return None
        return {
            "direction": "buy",
            "entry_price": entry,
            "sl": sl,
            "tp1": tp,
            "tp2": tp,
            "tp3": tp,
            "delivery_target": tp,
            "reason": "Simplified bullish OB midpoint",
        }
    except Exception as exc:
        log.debug("_try_ob: %s", exc)
        return None


def _try_rsi_range(df_m15: pd.DataFrame, params: dict, delivery: dict, symbol: str) -> dict[str, Any] | None:
    rsi = ta.rsi(df_m15["close"].astype(float), length=14)
    r = float(rsi.iloc[-1, 0])
    buy_lv = float(params.get("rsi_buy_level", 38))
    sell_lv = float(params.get("rsi_sell_level", 62))
    last = df_m15.iloc[-1]
    close = float(last["close"])
    pip = pip_size(symbol)
    lo = float(delivery["asian_low"])
    hi = float(delivery["asian_high"])
    if r < buy_lv and close <= lo + 10 * pip:
        tp = _nearest_target_above(close, [hi, float(delivery["previous_day_high"])])
        sl = close - float(params.get("rrr_min", 1.8)) * abs(tp - close) / max(float(params.get("rrr_target", 2.5)), 0.5)
        return {
            "direction": "buy",
            "entry_price": close,
            "sl": sl,
            "tp1": tp,
            "tp2": tp,
            "tp3": tp,
            "delivery_target": tp,
            "reason": "RSI oversold at range support",
        }
    if r > sell_lv and close >= hi - 10 * pip:
        tp = _nearest_target_below(close, [lo, float(delivery["previous_day_low"])])
        sl = close + float(params.get("rrr_min", 1.8)) * abs(close - tp) / max(float(params.get("rrr_target", 2.5)), 0.5)
        return {
            "direction": "sell",
            "entry_price": close,
            "sl": sl,
            "tp1": tp,
            "tp2": tp,
            "tp3": tp,
            "delivery_target": tp,
            "reason": "RSI overbought at range resistance",
        }
    return None


def _try_ema_pullback(df_m15: pd.DataFrame, df_h1: pd.DataFrame, params: dict, delivery: dict) -> dict[str, Any] | None:
    period = int(params.get("ema_period", 21))
    ema = ta.ema(df_h1["close"].astype(float), length=period)
    e_last = float(ema.iloc[-1, 0])
    last_m = float(df_m15["close"].iloc[-1])
    rsi = float(ta.rsi(df_m15["close"].astype(float), length=14).iloc[-1, 0])
    rmin = float(params.get("entry_rsi_min", 42))
    rmax = float(params.get("entry_rsi_max", 55))
    if last_m > e_last and rmin <= rsi <= rmax:
        tp = _nearest_target_above(last_m, [delivery["previous_day_high"], delivery["asian_high"]])
        sl = last_m - (tp - last_m) / float(params.get("rrr_min", 2.0))
        return {
            "direction": "buy",
            "entry_price": last_m,
            "sl": sl,
            "tp1": tp,
            "tp2": tp,
            "tp3": tp,
            "delivery_target": tp,
            "reason": "Pullback to H1 EMA, RSI supportive",
        }
    if last_m < e_last and rmin <= rsi <= rmax:
        tp = _nearest_target_below(last_m, [delivery["previous_day_low"], delivery["asian_low"]])
        sl = last_m + (last_m - tp) / float(params.get("rrr_min", 2.0))
        return {
            "direction": "sell",
            "entry_price": last_m,
            "sl": sl,
            "tp1": tp,
            "tp2": tp,
            "tp3": tp,
            "delivery_target": tp,
            "reason": "Pullback below H1 EMA, RSI supportive",
        }
    return None


def _try_momentum_breakout(df_m15: pd.DataFrame, params: dict, delivery: dict) -> dict[str, Any] | None:
    last = df_m15.iloc[-1]
    vol_m = float(df_m15["volume"].astype(float).tail(20).mean())
    if float(last["volume"]) < vol_m * float(params.get("volume_multiplier", 1.8)):
        return None
    hi20 = float(df_m15["high"].astype(float).tail(20).max())
    close = float(last["close"])
    if close >= hi20 - 1e-9:
        tp = _nearest_target_above(close, [delivery["nearest_round_50"], delivery["previous_day_high"]])
        sl = close - (tp - close) / float(params.get("rrr_min", 2.0))
        return {
            "direction": "buy",
            "entry_price": close,
            "sl": sl,
            "tp1": tp,
            "tp2": tp,
            "tp3": tp,
            "delivery_target": tp,
            "reason": "Momentum breakout with volume",
        }
    return None


def generate_signal(
    df_m15: pd.DataFrame,
    df_h1: pd.DataFrame,
    regime_label: str,
    strategies: list[str],
    symbol: str = "XAUUSD",
    tick: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Evaluate each allowed strategy; return first valid signal dict or None.
    """
    try:
        sym = str(symbol).upper()
        lab = str(regime_label).strip().upper()
        if lab in ("SKIP", "Q4") and not any(s == "manipulation_reversal" for s in strategies):
            pass

        delivery = compute_delivery_targets(df_m15, df_h1, sym)
        atr_s = ta.atr(df_m15["high"].astype(float), df_m15["low"].astype(float), df_m15["close"].astype(float), length=14)
        atr = float(atr_s.iloc[-1, 0])

        for sname in strategies:
            params = get_strategy_params(sname)
            if not params.get("enabled", True):
                continue
            raw: dict[str, Any] | None = None
            if sname == "manipulation_reversal":
                raw = _try_manipulation(df_m15, params, delivery, sym)
            elif sname == "ob_continuation":
                raw = _try_ob(df_h1, params, delivery, "buy", sym)
            elif sname == "rsi_reversal":
                raw = _try_rsi_range(df_m15, params, delivery, sym)
            elif sname in ("trend_pullback", "ema_continuation"):
                raw = _try_ema_pullback(df_m15, df_h1, params, delivery)
            elif sname == "momentum_breakout":
                raw = _try_momentum_breakout(df_m15, params, delivery)

            if not raw:
                continue

            rrr_min = float(params.get("rrr_min", 2.0))
            entry = float(raw["entry_price"])
            sl = float(raw["sl"])
            tp1 = float(raw["tp1"])
            risk = abs(entry - sl)
            reward = abs(tp1 - entry)
            if risk < 1e-9 or reward / risk < rrr_min * 0.95:
                continue

            out = {
                "symbol": sym,
                "direction": raw["direction"],
                "strategy": sname,
                "regime": lab,
                "entry_price": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": float(raw["tp2"]),
                "tp3": float(raw["tp3"]),
                "delivery_target": float(raw["delivery_target"]),
                "atr": atr,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": str(raw.get("reason", "")),
            }
            out["delivery_meta"] = delivery
            return out

        return None
    except Exception as exc:
        log.exception("generate_signal failed: %s", exc)
        return None
