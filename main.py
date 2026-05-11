"""
QUANT ENGINE — single-process orchestrator (15m cycle). No Redis.

Reads MT5 via core.data_feed, regime via core.regime_detector, YAML config only.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from core.cost_guard import check_cost
from core.data_feed import (
    get_account_info,
    get_candles,
    get_current_tick,
    get_h1_candles,
    is_mt5_connected,
)
from core.executor import execute_trade, log_trade
from core.kill_switch import check_kill_conditions
from core.regime_detector import detect_regime
from core.risk_manager import check_risk, load_trade_log
from core.scorer import min_score_to_trade, score_signal
from core.signal_engine import generate_signal
from core.strategy_map import get_strategies

_ROOT = Path(__file__).resolve().parent
_STATE_DIR = _ROOT / "state"
_SYSTEM_STATE = _STATE_DIR / "system_state.json"
_REGIMES_PATH = _ROOT / "config" / "regimes.yaml"


def write_state(data: dict) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _SYSTEM_STATE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logging.error("write_state failed: %s", exc)


def _stamp(payload: dict, account: dict | None = None) -> dict:
    out = dict(payload)
    out.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    if account is not None:
        out["account"] = {
            "balance": account.get("balance"),
            "equity": account.get("equity"),
            "currency": account.get("currency"),
        }
    return out


def _min_conf_from_yaml() -> float:
    try:
        if _REGIMES_PATH.exists():
            cfg = yaml.safe_load(_REGIMES_PATH.read_text(encoding="utf-8")) or {}
            return float(cfg.get("thresholds", {}).get("min_confidence_to_trade", 60))
    except Exception:
        pass
    return 60.0


def run_cycle() -> None:
    sym = os.getenv("SYMBOL_DEFAULT", "XAUUSD").strip().upper()

    if not is_mt5_connected():
        logging.error("MT5 not connected — waiting 60s")
        write_state({"status": "MT5_DISCONNECTED", "timestamp": datetime.now(timezone.utc).isoformat()})
        time.sleep(60)
        return

    trade_log = load_trade_log()
    try:
        account = get_account_info()
    except Exception as exc:
        logging.error("account info: %s", exc)
        write_state({"status": "ACCOUNT_ERROR", "error": str(exc)})
        return

    if check_kill_conditions(account, trade_log):
        write_state(_stamp({"status": "KILL_SWITCH_ACTIVE"}, account))
        return

    try:
        import MetaTrader5 as mt5

        df_m15 = get_candles(sym, mt5.TIMEFRAME_M15, 500)
        df_h1 = get_h1_candles(sym, 200)
        tick = get_current_tick(sym)
    except Exception as exc:
        logging.error("data fetch: %s", exc)
        write_state(_stamp({"status": "DATA_ERROR", "error": str(exc)}, account))
        return

    try:
        regime = detect_regime(df_m15, df_h1)
    except Exception as exc:
        logging.exception("regime: %s", exc)
        write_state(_stamp({"status": "REGIME_ERROR", "error": str(exc)}, account))
        return

    min_conf = _min_conf_from_yaml()
    if float(regime.get("confidence", 0)) < min_conf:
        write_state(_stamp({"status": "LOW_CONFIDENCE", "regime": regime}, account))
        return

    if regime.get("label") == "SKIP":
        write_state(_stamp({"status": "DEAD_SESSION", "regime": regime}, account))
        return

    ok_spread, cost_reason, spread_val = check_cost(sym, tick)
    if not ok_spread:
        write_state(_stamp({"status": "SPREAD_HIGH", "reason": cost_reason, "spread_pips": spread_val}, account))
        return

    strategies = get_strategies(str(regime["label"]))
    if not strategies:
        write_state(_stamp({"status": "NO_STRATEGY_FOR_REGIME", "regime": regime}, account))
        return

    signal = generate_signal(df_m15, df_h1, str(regime["label"]), strategies, symbol=sym, tick=tick)
    if signal is None:
        write_state(_stamp({"status": "NO_SIGNAL", "regime": regime, "spread_pips": spread_val}, account))
        return

    signal["spread_pips"] = spread_val
    sc, reasons = score_signal(signal, df_m15, regime)
    signal["score"] = sc
    signal["score_reasons"] = reasons

    if sc < min_score_to_trade():
        write_state(_stamp({"status": "LOW_SCORE", "regime": regime, "signal": signal}, account))
        return

    risk_ok, risk_reason, lot = check_risk(signal, account, trade_log)
    if not risk_ok:
        write_state(_stamp({"status": "RISK_BLOCKED", "reason": risk_reason, "signal": signal, "regime": regime}, account))
        return

    result = execute_trade(signal, lot, account)
    log_trade(signal, lot, result)

    write_state(
        _stamp(
            {
                "status": "TRADE_EXECUTED" if result.get("ok") else "EXECUTION_FAILED",
                "regime": regime,
                "signal": signal,
                "score": sc,
                "lot_size": lot,
                "result": result,
            },
            account,
        )
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    logging.info("QUANT ENGINE STARTING (no Redis, YAML + MT5 + JSON state)")
    while True:
        try:
            run_cycle()
        except Exception as e:
            logging.error("Cycle error: %s", e)
            write_state({"status": "ERROR", "error": str(e), "timestamp": datetime.now(timezone.utc).isoformat()})
        time.sleep(900)


if __name__ == "__main__":
    main()
