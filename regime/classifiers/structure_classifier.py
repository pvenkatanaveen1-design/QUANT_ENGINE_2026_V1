from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StructureClassification:
    structure_label: str
    swing_state: str


def classify_structure(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    breakout_sensitivity: float = 1.0,
    consolidation_length: int = 20,
    swing_lookback: int = 6,
) -> StructureClassification:
    if len(closes) < consolidation_length:
        return StructureClassification("UNKNOWN", "UNKNOWN")
    hi = max(highs[-consolidation_length:-1])
    lo = min(lows[-consolidation_length:-1])
    rng = max(1e-9, hi - lo)
    c = closes[-1]

    if c > hi + (rng * 0.01 * breakout_sensitivity) or c < lo - (rng * 0.01 * breakout_sensitivity):
        return StructureClassification("BREAKOUT", "EXPANSION")
    if rng / max(1e-9, c) < 0.002:
        return StructureClassification("COMPRESSION", "BALANCING")
    if len(closes) >= swing_lookback and closes[-1] > closes[-3] > closes[-swing_lookback]:
        return StructureClassification("TREND", "HH_HL")
    if len(closes) >= swing_lookback and closes[-1] < closes[-3] < closes[-swing_lookback]:
        return StructureClassification("TREND", "LH_LL")
    if len(closes) >= 4 and ((closes[-1] > closes[-2] < closes[-3]) or (closes[-1] < closes[-2] > closes[-3])):
        return StructureClassification("EXHAUSTION", "SWING_FAIL")
    return StructureClassification("RANGE", "BALANCE")

