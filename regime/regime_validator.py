from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ValidationResult:
    accepted: bool
    reason: str


def validate_regime(
    *,
    confidence: float,
    bars_in_regime: int,
    min_confidence: float = 0.70,
    min_bars: int = 3,
    flip_cooldown: int = 0,
    bars_since_last_flip: int = 9999,
) -> ValidationResult:
    if confidence < min_confidence:
        return ValidationResult(False, f"confidence_below_threshold:{confidence:.2f}<{min_confidence:.2f}")
    if bars_in_regime < min_bars:
        return ValidationResult(False, f"insufficient_persistence:{bars_in_regime}<{min_bars}")
    if bars_since_last_flip < flip_cooldown:
        return ValidationResult(False, f"cooldown_active:{bars_since_last_flip}<{flip_cooldown}")
    return ValidationResult(True, "accepted")

