"""
Operator and automated kill-switch persistence. No Redis.

Writes ``state/kill_switch.json`` and append-only ``logs/kill_events.csv``.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_STATE_DIR = _ROOT / "state"
_KILL_PATH = _STATE_DIR / "kill_switch.json"
_LOG_DIR = _ROOT / "logs"
_KILL_EVENTS = _LOG_DIR / "kill_events.csv"
_TRADES = _LOG_DIR / "trades.csv"


def _ensure_dirs() -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)


def is_kill_active() -> bool:
    try:
        if not _KILL_PATH.exists():
            return False
        data = json.loads(_KILL_PATH.read_text(encoding="utf-8"))
        return bool(data.get("active"))
    except Exception as exc:
        log.warning("is_kill_active: %s", exc)
        return False


def reset_kill_switch() -> None:
    try:
        _ensure_dirs()
        payload = {"active": False, "reason": "", "updated": datetime.now(timezone.utc).isoformat()}
        _KILL_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log.info("kill_switch reset")
    except Exception as exc:
        log.exception("reset_kill_switch: %s", exc)


def _write_kill(reason: str, details: dict[str, Any]) -> None:
    _ensure_dirs()
    payload = {
        "active": True,
        "reason": reason,
        "updated": datetime.now(timezone.utc).isoformat(),
        "details": details,
    }
    _KILL_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    row = {**{"timestamp": payload["updated"], "reason": reason}, **{f"d_{k}": v for k, v in details.items()}}
    write_header = not _KILL_EVENTS.exists()
    with open(_KILL_EVENTS, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            w.writeheader()
        w.writerow(row)


def check_kill_conditions(account_info: dict[str, Any], trade_log: pd.DataFrame) -> bool:
    """
    Return True if engine should enter kill (writes kill_switch.json).
    """
    try:
        balance = float(account_info.get("balance", 0))
        equity = float(account_info.get("equity", balance))
        if balance <= 0:
            return False

        if equity < balance * 0.97:
            _write_kill("equity_below_97pct_balance", {"equity": equity, "balance": balance})
            return True

        if trade_log is not None and len(trade_log) > 0 and "pnl" in trade_log.columns:
            today = datetime.now(timezone.utc).date()
            if "timestamp" in trade_log.columns:
                tl = trade_log.copy()
                tl["dt"] = pd.to_datetime(tl["timestamp"], errors="coerce", utc=True)
                day = tl[tl["dt"].dt.date == today]
            else:
                day = trade_log
            pnl_sum = pd.to_numeric(day["pnl"], errors="coerce").fillna(0).sum()
            if pnl_sum <= -balance * 0.025:
                _write_kill("daily_loss_2_5pct", {"pnl_today": pnl_sum})
                return True

            tail = trade_log.tail(3)
            if len(tail) >= 3:
                if (pd.to_numeric(tail["pnl"], errors="coerce").fillna(0) < 0).all():
                    if "timestamp" in tail.columns:
                        ts = pd.to_datetime(tail["timestamp"], errors="coerce", utc=True)
                        if (ts.max() - ts.min()).total_seconds() <= 7200:
                            _write_kill("3_losses_2h", {})
                            return True

        # Spread / signal degradation stubs
        return False
    except Exception as exc:
        log.exception("check_kill_conditions: %s", exc)
        return False
