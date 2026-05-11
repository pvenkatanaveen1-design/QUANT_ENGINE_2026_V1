from __future__ import annotations


def structure_vote(closes: list[float], lookback: int = 48) -> float | None:
    if len(closes) < lookback + 1:
        return None
    window = closes[-lookback:]
    slope = window[-1] - window[0]
    atr_proxy = sum(abs(window[i + 1] - window[i]) for i in range(len(window) - 1)) / (len(window) - 1)
    if atr_proxy <= 0:
        return None
    norm = slope / (atr_proxy * lookback ** 0.5)
    return float(max(-1.0, min(1.0, norm)))
