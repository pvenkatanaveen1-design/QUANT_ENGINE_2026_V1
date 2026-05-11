"""
Spread / cost gate. No Redis. Tracks last 20 spread readings in-process.
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_RISK_PATH = _ROOT / "config" / "risk.yaml"
_SPREAD_HISTORY: deque[float] = deque(maxlen=20)


def _load_risk() -> dict:
    try:
        if _RISK_PATH.exists():
            with open(_RISK_PATH, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception as exc:
        log.warning("cost_guard risk yaml: %s", exc)
    return {"risk": {"max_spread_pips": 2.5}}


def pip_size(symbol: str) -> float:
    u = str(symbol).upper()
    if u.startswith("XAU") or u.startswith("XAG"):
        return 0.01
    if "JPY" in u:
        return 0.01
    if u.startswith("BTC"):
        return 1.0
    return 0.0001


def check_cost(symbol: str, tick: dict[str, Any], pair_config: dict[str, Any] | None = None) -> tuple[bool, str, float]:
    """
    Returns (approved, reason, spread_pips).
    ``pair_config`` reserved for per-pair overrides (from YAML).
    """
    try:
        _ = pair_config
        sym = str(symbol).upper()
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        pip = pip_size(sym)
        if pip <= 0:
            return False, "invalid pip size", 0.0
        spread_pips = (ask - bid) / pip

        cfg = _load_risk().get("risk", {})
        max_spread = float(cfg.get("max_spread_pips", 2.5))

        if spread_pips > max_spread:
            return False, f"spread {spread_pips:.2f} pips > max {max_spread}", spread_pips

        _SPREAD_HISTORY.append(spread_pips)
        if len(_SPREAD_HISTORY) >= 5:
            avg = sum(_SPREAD_HISTORY) / len(_SPREAD_HISTORY)
            if spread_pips > 2.0 * avg:
                return False, "spread abnormally wide vs recent average", spread_pips

        return True, "ok", spread_pips
    except Exception as exc:
        log.exception("check_cost: %s", exc)
        return False, f"cost check error: {exc}", 0.0
