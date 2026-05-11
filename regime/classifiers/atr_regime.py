from __future__ import annotations

from features.atr import atr_rma


def atr_vote(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    atr = atr_rma(highs, lows, closes, period=period)
    if atr is None or not closes:
        return None
    baseline = atr / closes[-1] if closes[-1] else 0.0
    if baseline < 0.0005:
        return -0.3
    if baseline < 0.0012:
        return 0.0
    return 0.55
