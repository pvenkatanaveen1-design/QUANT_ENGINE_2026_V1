from __future__ import annotations


def _pip_size(symbol: str) -> float:
    s = symbol.upper()
    if "JPY" in s:
        return 0.01
    if s.startswith("XAU"):
        return 0.1
    return 0.0001


def spread_in_pips(symbol: str, bid: float, ask: float) -> float:
    pip = _pip_size(symbol)
    return (ask - bid) / pip


def spread_ok(symbol: str, bid: float, ask: float, pair_cfg: dict) -> tuple[bool, float]:
    max_pips = float(pair_cfg.get("min_spread_pips", 2.5))
    sp = spread_in_pips(symbol, bid, ask)
    return sp <= max_pips, sp
