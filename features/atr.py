from __future__ import annotations


def true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr_rma(highs: list[float], lows: list[float], closes: list[float], period: int) -> float | None:
    if period <= 1 or len(closes) < period + 1:
        return None
    seed_tr: list[float] = []
    for i in range(1, period + 1):
        seed_tr.append(true_range(highs[i], lows[i], closes[i - 1]))
    atr = sum(seed_tr) / period
    for i in range(period + 1, len(closes)):
        tr = true_range(highs[i], lows[i], closes[i - 1])
        atr = ((period - 1) * atr + tr) / period
    return atr
