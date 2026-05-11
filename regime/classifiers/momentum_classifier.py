from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MomentumClassification:
    rsi_value: float
    momentum_label: str


def calculate_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains = []
    losses = []
    for i in range(-period, 0):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(abs(min(0.0, d)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def classify_momentum(
    closes: list[float],
    *,
    overbought: float = 70.0,
    oversold: float = 30.0,
    neutral_low: float = 45.0,
    neutral_high: float = 55.0,
) -> MomentumClassification:
    rsi = round(calculate_rsi(closes), 2)
    if rsi >= overbought:
        label = "OVERBOUGHT"
    elif rsi <= oversold:
        label = "OVERSOLD"
    elif rsi >= neutral_high:
        label = "BULLISH_MOMENTUM"
    elif rsi <= neutral_low:
        label = "BEARISH_MOMENTUM"
    else:
        label = "NEUTRAL"
    return MomentumClassification(rsi_value=rsi, momentum_label=label)

