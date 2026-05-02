from __future__ import annotations

from schemas.regime import RegimeLabel, RegimeState


def score_signal(regime: RegimeState, spread_ok: bool, session_ok: bool) -> float:
    base = regime.confidence * 40.0
    if regime.label == RegimeLabel.TREND:
        base += 30.0
    if regime.label == RegimeLabel.RANGE:
        base += 10.0
    if spread_ok:
        base += 15.0
    if session_ok:
        base += 15.0
    return max(0.0, min(100.0, base))
