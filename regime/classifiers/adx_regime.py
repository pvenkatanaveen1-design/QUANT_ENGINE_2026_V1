from __future__ import annotations

from features.adx import adx_latest


def adx_vote(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    adx_val, spread = adx_latest(highs, lows, closes, period=period)
    if adx_val is None or spread is None:
        return None
    if adx_val < 18:
        return 0.0
    magnitude = max(-1.0, min(1.0, spread / 35.0))
    return magnitude * min(1.0, max(0.0, adx_val - 18) / 25.0)
