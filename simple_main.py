"""
Candle-only MT5 loop: multi-timeframe bars, regime, filters, signal JSON.

No Redis, no tick builder — uses MetaTrader5 rates and symbol_info bid/ask.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import pandas as pd
from dotenv import load_dotenv

from regime.classifiers.ensemble_regime import EnsembleRegimeClassifier
from regime.strategy_router import RegimeStrategyRouter
from market.features.atr import calculate_atr_from_candles
from risk.news_guard import _compute_news_fields, load_test_news_events
from systems.intelligence.session_filter import session_filter
from brain import publish_regime_after_classify
from core.state_bus import (
    write_system_state,
    write_signal,
    is_kill_active,
    get_kill_reason,
    write_heartbeat,
)

# --- timeframe → MT5 constant & loop cadence ---------------------------------
_TF_MT5 = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}

_TF_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
}

FETCH_ORDER = ("M1", "M5", "M15", "M30", "H1", "H4")


def _load_pair_settings(symbol: str) -> dict[str, Any]:
    path = Path(os.environ.get("PAIR_SETTINGS_PATH", "config/pair_settings.json"))
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and symbol in data:
                return dict(data[symbol])
        except Exception:
            pass
    return {
        "atr_period": int(os.environ.get("ATR_PERIOD", "14")),
        "max_spread_pips": float(os.environ.get("QUANT_MAX_SPREAD_PIPS", "40")),
    }


def get_candles(symbol: str, timeframe: int, bars: int = 500) -> pd.DataFrame | None:
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    if "tick_volume" in df.columns and "volume" not in df.columns:
        df["volume"] = df["tick_volume"]
    if "real_volume" in df.columns and df["real_volume"].fillna(0).sum() > 0:
        df["volume"] = df["real_volume"]
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[["time", "open", "high", "low", "close", "volume"]]


def fetch_all_tf(symbol: str, bars: int = 500) -> dict[str, pd.DataFrame | None]:
    out: dict[str, pd.DataFrame | None] = {}
    for name in FETCH_ORDER:
        out[name] = get_candles(symbol, _TF_MT5[name], bars)
    return out


def passes_news_gate(symbol: str, now_utc: datetime) -> bool:
    _ = symbol
    fields = _compute_news_fields(load_test_news_events(now_utc), now_utc)
    return fields.get("news:blackout") != "ACTIVE"


def spread_ok_from_info(symbol: str, pair_cfg: dict[str, Any]) -> tuple[bool, float]:
    info = mt5.symbol_info(symbol)
    if info is None:
        return False, 0.0
    bid, ask = float(info.bid), float(info.ask)
    if bid <= 0 or ask <= 0:
        return False, 0.0
    width = ask - bid
    sym = symbol.upper()
    if "XAU" in sym or "GOLD" in sym:
        pips = width / 0.10
    elif "JPY" in sym:
        pips = width / 0.01
    elif info.digits in (5, 3):
        pips = width / (info.point * 10)
    else:
        pips = width / (info.point * 10) if info.point else width / 0.0001
    max_spread = float(pair_cfg.get("max_spread_pips", 40))
    return pips <= max_spread, round(float(pips), 2)


def asian_hi_lo(highs: list[float], lows: list[float], asian_len: int = 80) -> tuple[float, float]:
    n = min(asian_len, len(highs), len(lows))
    if n < 2:
        return max(highs[-1], lows[-1]), min(highs[-1], lows[-1])
    return max(highs[-n:]), min(lows[-n:])


@dataclass
class RangeLevels:
    prior_day_high: float | None
    prior_day_low: float | None


def prior_range_from_mids(mids: deque[float], window: int = 12000) -> RangeLevels:
    if len(mids) < 2:
        return RangeLevels(None, None)
    chunk = list(mids)[-min(window, len(mids)) :]
    return RangeLevels(max(chunk), min(chunk))


def score_signal(regime: Any, spread_ok_flag: bool, session_ok: bool) -> float:
    base = 40.0
    conf = float(getattr(regime, "confidence", 0) or 0)
    base += conf * 35.0
    if spread_ok_flag:
        base += 12.0
    if session_ok:
        base += 13.0
    return min(100.0, round(base, 2))


@dataclass
class AlphaCandidate:
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    strategy: str = "alpha_sweep"
    confluence_score: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "strategy": self.strategy,
            "confluence_score": self.confluence_score,
            "extras": dict(self.extras),
        }


def maybe_generate_signal(
    symbol: str,
    regime: Any,
    last_mid: float,
    sweep_high: float,
    sweep_low: float,
    *,
    strategy: str | None = None,
    strategy_score: float | None = None,
) -> AlphaCandidate | None:
    span = max(sweep_high - sweep_low, 1e-6)
    buf = span * 0.0005
    label = str(getattr(getattr(regime, "label", regime), "value", regime))
    strat = strategy if strategy else "alpha_sweep"
    extras: dict[str, Any] = {"regime_hint": label}
    if strategy is not None:
        extras["selected_strategy"] = strategy
    if strategy_score is not None:
        extras["strategy_score"] = float(strategy_score)

    if last_mid > sweep_high + buf:
        entry = last_mid
        sl = sweep_low - buf
        tp = entry + (entry - sl) * 2.0
        if sl >= entry:
            return None
        return AlphaCandidate(symbol, "buy", entry, sl, tp, strategy=strat, extras=dict(extras))

    if last_mid < sweep_low - buf:
        entry = last_mid
        sl = sweep_high + buf
        tp = entry - (sl - entry) * 2.0
        if sl <= entry:
            return None
        return AlphaCandidate(symbol, "sell", entry, sl, tp, strategy=strat, extras=dict(extras))

    return None


def _candles_meta(dfs: dict[str, pd.DataFrame | None]) -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = {}
    for name, df in dfs.items():
        if df is None or len(df) == 0:
            meta[name] = {"bars": 0, "last_time": None}
        else:
            ts = df["time"].iloc[-1]
            meta[name] = {"bars": int(len(df)), "last_time": ts.isoformat()}
    return meta


def run_cycle(symbol: str, regime_model: EnsembleRegimeClassifier, selected_tf: str) -> None:
    if is_kill_active():
        write_system_state({"status": "KILL_ACTIVE", "reason": get_kill_reason(), "symbol": symbol})
        return

    pair_cfg = _load_pair_settings(symbol)
    dfs = fetch_all_tf(symbol, bars=600)
    df_op = dfs.get(selected_tf)
    df_h1 = dfs.get("H1")
    df_h4 = dfs.get("H4")

    if df_op is None or len(df_op) < 80 or df_h1 is None or len(df_h1) < 80:
        write_system_state(
            {
                "status": "INSUFFICIENT_DATA",
                "symbol": symbol,
                "selected_tf": selected_tf,
                "candles_meta": _candles_meta(dfs),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return

    info = mt5.symbol_info(symbol)
    if info is None:
        write_system_state({"status": "NO_SYMBOL", "symbol": symbol})
        return

    highs = df_h1["high"].tolist()
    lows = df_h1["low"].tolist()
    closes = df_h1["close"].tolist()
    now_utc = datetime.now(timezone.utc)

    regime = regime_model.classify(highs, lows, closes, time_utc=now_utc)
    publish_regime_after_classify(symbol, regime)

    atr_period = int(pair_cfg.get("atr_period", 14))
    atr_val = calculate_atr_from_candles(
        df_op["high"].tolist(),
        df_op["low"].tolist(),
        df_op["close"].tolist(),
        period=atr_period,
    )

    mids = ((df_h1["high"] + df_h1["low"]) / 2).tolist()
    mids_deque = deque(mids[-12000:])
    rng_lvls = prior_range_from_mids(mids_deque, window=12000)
    as_hi, as_lo = asian_hi_lo(highs, lows, asian_len=80)

    session_ok = session_filter.is_tradeable()
    news_ok = passes_news_gate(symbol, now_utc)
    spread_ok_flag, spread_val = spread_ok_from_info(symbol, pair_cfg)

    regime_label = regime.label.value if hasattr(regime.label, "value") else str(regime.label)
    regime_conf = float(getattr(regime, "confidence", 0.0))

    bid, ask = float(info.bid), float(info.ask)
    current_price = (bid + ask) / 2 if bid > 0 and ask > 0 else float(df_op["close"].iloc[-1])

    system_state: dict[str, Any] = {
        "status": "RUNNING",
        "symbol": symbol,
        "selected_tf": selected_tf,
        "regime": regime_label,
        "regime_confidence": regime_conf,
        "session_ok": session_ok,
        "news_ok": news_ok,
        "spread_ok": spread_ok_flag,
        "spread_pips": spread_val,
        "atr": atr_val,
        "prev_day_high": rng_lvls.prior_day_high,
        "prev_day_low": rng_lvls.prior_day_low,
        "asian_high": as_hi,
        "asian_low": as_lo,
        "current_price": current_price,
        "candles_meta": _candles_meta(dfs),
        "timestamp": now_utc.isoformat(),
    }
    write_system_state(system_state)
    write_heartbeat("simple_main")

    if not spread_ok_flag or not session_ok or not news_ok:
        write_system_state(
            {
                **system_state,
                "status": "FILTERED_OUT",
                "filter_reason": f"spread={spread_ok_flag} session={session_ok} news={news_ok}",
            }
        )
        return

    score = score_signal(regime, spread_ok_flag, session_ok)
    sweep_high = rng_lvls.prior_day_high or as_hi or max(highs[-50:])
    sweep_low = rng_lvls.prior_day_low or as_lo or min(lows[-50:])

    atr_cur = float(atr_val) if atr_val is not None else 0.0
    strategy_name, strategy_router_score = RegimeStrategyRouter.best_strategy(
        regime_label, atr_cur, spread_val
    )
    logging.info(
        "RegimeStrategyRouter selected strategy=%s score=%.2f regime=%s atr=%.6f spread_pips=%.2f",
        strategy_name,
        strategy_router_score,
        regime_label,
        atr_cur,
        spread_val,
    )

    candidate = maybe_generate_signal(
        symbol,
        regime,
        current_price,
        sweep_high,
        sweep_low,
        strategy=strategy_name,
        strategy_score=strategy_router_score,
    )

    if candidate is None:
        write_system_state({**system_state, "status": "NO_SIGNAL"})
        return

    candidate.confluence_score = float(score)
    candidate.extras["atr_estimate"] = float(atr_val) if atr_val else 1.5
    candidate.extras["spread_pips"] = float(spread_val)
    candidate.extras["regime"] = regime_label
    candidate.extras["prev_day_high"] = rng_lvls.prior_day_high
    candidate.extras["prev_day_low"] = rng_lvls.prior_day_low
    candidate.extras["asian_high"] = as_hi
    candidate.extras["asian_low"] = as_lo

    min_conf = float(os.environ.get("QUANT_MIN_CONFLUENCE", "58"))
    if candidate.confluence_score < min_conf:
        write_system_state({**system_state, "status": "LOW_SCORE", "score": score})
        return

    write_signal(candidate.to_dict())
    write_system_state(
        {
            **system_state,
            "status": "SIGNAL_READY",
            "score": score,
            "direction": candidate.direction,
        }
    )
    logging.info(
        "SIGNAL direction=%s score=%s regime=%s tf=%s",
        candidate.direction,
        score,
        regime_label,
        selected_tf,
    )


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    load_dotenv()

    mt5.initialize()
    if not mt5.terminal_info():
        logging.error("MT5 not connected. Open MT5 first.")
        return

    symbol = os.environ.get("SYMBOL_DEFAULT", "XAUUSD")
    selected_tf = os.environ.get("SELECTED_TF", "M15").upper()
    if selected_tf not in _TF_MT5:
        logging.warning("Unknown SELECTED_TF=%s — defaulting to M15", selected_tf)
        selected_tf = "M15"

    sleep_s = int(os.environ.get("CYCLE_SECONDS", _TF_SECONDS.get(selected_tf, 900)))

    regime_model = EnsembleRegimeClassifier()
    session_filter.start()

    logging.info("SIMPLE_MAIN started symbol=%s tf=%s sleep_s=%s", symbol, selected_tf, sleep_s)

    while True:
        try:
            run_cycle(symbol, regime_model, selected_tf)
        except Exception as e:
            logging.exception("Cycle error: %s", e)
            write_system_state(
                {
                    "status": "ERROR",
                    "symbol": symbol,
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
