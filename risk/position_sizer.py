from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.paths import project_root


def load_pair_settings(path: Path | None = None) -> dict[str, Any]:
    p = path or (project_root() / "config" / "pair_settings.json")
    return json.loads(p.read_text(encoding="utf-8"))


def volume_lots_for_risk(
    symbol: str,
    equity: float,
    risk_pct: float,
    stop_distance_price: float,
    settings: dict[str, Any],
) -> float:
    cfg = settings.get(symbol, {})
    pip_value = float(cfg.get("pip_value_per_lot", 10.0))
    max_lots = float(cfg.get("max_position_size_lots", 0.1))
    pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
    sl_pips = abs(stop_distance_price) / pip_size if pip_size else 0.0
    if sl_pips <= 0:
        return 0.0
    risk_money = equity * (risk_pct / 100.0)
    denom = sl_pips * pip_value
    if denom <= 0:
        return 0.0
    return max(0.0, min(max_lots, risk_money / denom))
