from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AdxClassification:
    adx_value: float
    adx_slope: float
    trend_strength: str


def classify_adx(
    adx_value: float,
    prev_adx: float | None = None,
    *,
    weak_threshold: float = 25.0,
    strong_threshold: float = 30.0,
    no_trend_threshold: float = 20.0,
) -> AdxClassification:
    slope = 0.0 if prev_adx is None else adx_value - prev_adx
    if adx_value >= strong_threshold:
        strength = "STRONG_TREND"
    elif adx_value >= weak_threshold:
        strength = "WEAK_TREND"
    elif adx_value < no_trend_threshold:
        strength = "NO_TREND"
    else:
        strength = "TRANSITIONAL"
    return AdxClassification(adx_value=adx_value, adx_slope=slope, trend_strength=strength)

