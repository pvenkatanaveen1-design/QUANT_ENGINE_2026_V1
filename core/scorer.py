"""
Confluence scoring 0–10 (capped from raw 12). No Redis.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_RISK_PATH = _ROOT / "config" / "risk.yaml"
_IST = timezone(timedelta(hours=5, minutes=30))


def _load_risk() -> dict:
    try:
        if _RISK_PATH.exists():
            with open(_RISK_PATH, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception as exc:
        log.warning("risk yaml: %s", exc)
    return {"risk": {"min_signal_score": 6}}


def _in_kill_zone_ist(now_ist: datetime) -> bool:
    t = now_ist.time()
    from datetime import time as dtime

    return (dtime(2, 0) <= t <= dtime(5, 0)) or (dtime(12, 0) <= t <= dtime(14, 0))


def score_signal(signal: dict[str, Any], df_m15: pd.DataFrame, regime: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """
    Returns (score 0-10, dict with passed/failed reason lists).
    """
    try:
        raw = 0
        passed: list[str] = []
        failed: list[str] = []

        rsi_s = __import__("pandas_ta").rsi(df_m15["close"].astype(float), length=14)
        rsi = float(rsi_s.dropna().iloc[-1])
        d = str(signal.get("direction", "")).lower()
        if d == "buy" and 42 <= rsi <= 60:
            raw += 2
            passed.append("RSI aligned (buy)")
        elif d == "sell" and 40 <= rsi <= 58:
            raw += 2
            passed.append("RSI aligned (sell)")
        else:
            failed.append("RSI alignment")

        lab = str(regime.get("label", "")).upper()
        adx = float(regime.get("metrics", {}).get("adx_h1", 0))
        if lab in ("Q1", "Q2") and adx >= 28:
            raw += 2
            passed.append("ADX confirms trend regime")
        elif lab == "Q3" and adx < 25:
            raw += 2
            passed.append("ADX confirms range regime")
        else:
            failed.append("ADX regime fit")

        vol = float(df_m15["volume"].astype(float).iloc[-1])
        vma = float(df_m15["volume"].astype(float).tail(20).mean())
        if vma > 0 and vol >= 1.2 * vma:
            raw += 2
            passed.append("Volume confirmation")
        else:
            failed.append("Volume confirmation")

        now_ist = datetime.now(_IST)
        if _in_kill_zone_ist(now_ist):
            raw += 2
            passed.append("Session kill zone")
        else:
            failed.append("Kill zone session")

        last3 = df_m15.tail(3)["close"].astype(float)
        if d == "buy" and last3.is_monotonic_increasing:
            raw += 2
            passed.append("Short-term structure")
        elif d == "sell" and last3.is_monotonic_decreasing:
            raw += 2
            passed.append("Short-term structure")
        else:
            failed.append("Structure (3 closes)")

        tp = float(signal.get("tp1", 0))
        entry = float(signal.get("entry_price", 0))
        sym = str(signal.get("symbol", "XAUUSD"))
        pip = 0.01 if sym.upper().startswith("XAU") else 0.0001
        if abs(tp - entry) <= 20 * pip:
            raw += 1
            passed.append("TP near delivery")
        else:
            failed.append("TP proximity bonus")

        # News bonus: placeholder (no calendar wired)
        raw += 1
        passed.append("News placeholder OK")

        display = min(10, raw)
        return int(display), {"passed": passed, "failed": failed, "raw": raw}
    except Exception as exc:
        log.exception("score_signal: %s", exc)
        return 0, {"passed": [], "failed": [str(exc)], "raw": 0}


def min_score_to_trade() -> int:
    return int(_load_risk().get("risk", {}).get("min_signal_score", 6))
