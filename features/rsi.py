from __future__ import annotations


def rsi_wilder(closes: list[float], period: int = 14) -> float | None:
    if period <= 1 or len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g = ch if ch > 0 else 0.0
        l = -ch if ch < 0 else 0.0
        avg_gain = ((period - 1) * avg_gain + g) / period
        avg_loss = ((period - 1) * avg_loss + l) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else None
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
