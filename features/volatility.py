from __future__ import annotations

import statistics


def return_std_pct(closes: list[float], window: int = 120) -> float | None:
    if len(closes) < window + 1:
        return None
    rets: list[float] = []
    for i in range(-window - 1, -1):
        a, b = closes[i], closes[i + 1]
        if b == 0:
            continue
        rets.append((b - a) / a * 100.0)
    if len(rets) < 10:
        return None
    return statistics.pstdev(rets)
