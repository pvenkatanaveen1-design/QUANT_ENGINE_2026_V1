"""
Pre-trade risk checks and position sizing. No Redis.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_RISK_PATH = _ROOT / "config" / "risk.yaml"
_KILL_PATH = _ROOT / "state" / "kill_switch.json"
_LOG_DIR = _ROOT / "logs"
_TRADES_CSV = _LOG_DIR / "trades.csv"


def _load_risk() -> dict:
    try:
        with open(_RISK_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        log.warning("risk.yaml missing: %s — defaults", exc)
        return {}


def load_trade_log() -> pd.DataFrame:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        if not _TRADES_CSV.exists():
            return pd.DataFrame()
        return pd.read_csv(_TRADES_CSV)
    except Exception as exc:
        log.warning("load_trade_log: %s", exc)
        return pd.DataFrame()


def _pip_value_usd(symbol: str) -> float:
    u = str(symbol).upper()
    if u.startswith("XAU"):
        return 10.0
    return 10.0


def check_risk(
    signal: dict[str, Any],
    account: dict[str, Any],
    trade_log: pd.DataFrame,
) -> tuple[bool, str, float]:
    """
    Sequential checks. Returns (approved, reason, lot_size).
    """
    try:
        cfg = _load_risk()
        r = cfg.get("risk", cfg)

        if _KILL_PATH.exists():
            try:
                kill = json.loads(_KILL_PATH.read_text(encoding="utf-8"))
                if kill.get("active"):
                    return False, str(kill.get("reason", "kill_switch")), 0.0
            except Exception as exc:
                log.warning("kill_switch read: %s", exc)

        balance = float(account.get("balance", 0))

        if trade_log is not None and len(trade_log) > 0:
            tl = trade_log.copy()
            if "timestamp" in tl.columns:
                tl["dt"] = pd.to_datetime(tl["timestamp"], errors="coerce", utc=True)
                today = datetime.now(timezone.utc).date()
                day_rows = tl[tl["dt"].dt.date == today]
            else:
                day_rows = tl

            if "pnl" in day_rows.columns:
                pnl_sum = pd.to_numeric(day_rows["pnl"], errors="coerce").fillna(0).sum()
                max_dd = float(r.get("max_daily_loss_pct", 0.03))
                if pnl_sum < -abs(balance * max_dd):
                    return False, "daily loss limit", 0.0

            max_tr = int(r.get("max_trades_per_day", 3))
            if len(day_rows) >= max_tr:
                return False, "max trades per day", 0.0

            n_stop = int(r.get("consecutive_loss_stop", 3))
            tail = tl.tail(n_stop)
            if len(tail) >= n_stop and "pnl" in tail.columns:
                vals = pd.to_numeric(tail["pnl"], errors="coerce").fillna(0)
                if (vals < 0).all():
                    return False, "consecutive loss stop", 0.0

        # News blackout placeholder — integrate CSV later
        cal = _ROOT / "data" / "economic_calendar.csv"
        if cal.exists():
            log.debug("economic calendar present — not parsed in this build")

        min_sc = int(r.get("min_signal_score", 6))
        if int(signal.get("score", 0)) < min_sc:
            return False, f"signal score {signal.get('score')} < {min_sc}", 0.0

        if trade_log is not None and "direction" in trade_log.columns and len(trade_log) > 0:
            opens = trade_log[trade_log.get("result", "") == "open"] if "result" in trade_log.columns else trade_log.iloc[0:0]
            if len(opens) > 0:
                same = opens[opens["direction"].str.lower() == str(signal.get("direction")).lower()]
                if len(same) > 0:
                    return False, "correlated open direction", 0.0

        sym = str(signal.get("symbol", "XAUUSD"))
        entry = float(signal["entry_price"])
        sl = float(signal["sl"])
        sl_dist = abs(entry - sl)
        pip = 0.01 if sym.upper().startswith("XAU") else 0.0001
        sl_pips = sl_dist / pip if pip else 1.0
        if sl_pips < 0.1:
            return False, "invalid stop distance", 0.0

        base_risk = float(r.get("base_risk_pct", 0.005))
        reg_mult = float(cfg.get("regime_size_multipliers", {}).get(str(signal.get("regime", "Q1")), 1.0))
        sc = int(signal.get("score", 0))
        if sc >= 9:
            sc_mult = 1.0
        elif sc >= 7:
            sc_mult = 0.85
        else:
            sc_mult = 0.7

        loss_mult = 1.0
        if trade_log is not None and len(trade_log) >= 1 and "pnl" in trade_log.columns:
            losses = 0
            for v in pd.to_numeric(trade_log.tail(3)["pnl"], errors="coerce").fillna(0).tolist()[::-1]:
                if v < 0:
                    losses += 1
                else:
                    break
            loss_mult = {0: 1.0, 1: 0.9, 2: 0.75}.get(losses, 0.75)

        pip_val = _pip_value_usd(sym)
        raw_lots = (balance * base_risk * reg_mult * sc_mult * loss_mult) / (sl_pips * pip_val)
        lot = round(max(0.01, min(raw_lots, 10.0)), 2)

        return True, "ok", float(lot)
    except Exception as exc:
        log.exception("check_risk: %s", exc)
        return False, str(exc), 0.0
