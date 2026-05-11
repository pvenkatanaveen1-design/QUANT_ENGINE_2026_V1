from __future__ import annotations


def detect_transition(previous_label: str | None, current_label: str, structure_label: str, atr_state: str) -> str:
    if not previous_label:
        return "BOOTSTRAP"
    if previous_label == current_label:
        if structure_label == "COMPRESSION" and atr_state == "EXPANDING":
            return "COMPRESSION_TO_EXPANSION"
        return "STABLE"
    if "RANGE" in previous_label and "BREAKOUT" in current_label:
        return "RANGE_TO_BREAKOUT"
    if "TREND" in previous_label and "RANGE" in current_label:
        return "TREND_TO_RANGE"
    if structure_label == "EXHAUSTION":
        return "EXHAUSTION_TO_REVERSAL"
    return "REGIME_FLIP"

