from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AtrClassification:
    atr_value: float
    atr_percentile: float
    volatility_class: str
    expansion_state: str


def classify_atr(
    atr_value: float,
    atr_percentile: float,
    prev_atr: float | None = None,
    *,
    low_percentile: float = 25.0,
    high_percentile: float = 80.0,
    chaotic_percentile: float = 95.0,
    expansion_change_pct: float = 0.05,
) -> AtrClassification:
    if atr_percentile >= chaotic_percentile:
        vol = "CHAOTIC_VOL"
    elif atr_percentile >= high_percentile:
        vol = "HIGH_VOL"
    elif atr_percentile <= low_percentile:
        vol = "LOW_VOL"
    else:
        vol = "NORMAL_VOL"

    if prev_atr is None:
        exp = "NEUTRAL"
    elif atr_value > prev_atr * (1.0 + expansion_change_pct):
        exp = "EXPANDING"
    elif atr_value < prev_atr * (1.0 - expansion_change_pct):
        exp = "COMPRESSING"
    else:
        exp = "STABLE"
    return AtrClassification(atr_value=atr_value, atr_percentile=atr_percentile, volatility_class=vol, expansion_state=exp)

