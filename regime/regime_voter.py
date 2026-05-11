from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from regime.probability_engine import normalize_votes, confidence_and_uncertainty

REGIME_12 = [
    "TREND_LOW_VOL",
    "TREND_HIGH_VOL",
    "RANGE_LOW_VOL",
    "RANGE_HIGH_VOL",
    "BREAKOUT_EXPANSION",
    "NEWS_CHAOS",
    "ASIA_COMPRESSION",
    "NY_REVERSAL",
    "LIQUIDITY_SWEEP",
    "PULLBACK_CONTINUATION",
    "EXHAUSTION",
    "TRANSITION",
]


@dataclass
class VoteOutput:
    regime_label: str
    probabilities: dict[str, float]
    confidence: float
    uncertainty: float
    vote_breakdown: dict[str, float]


def vote_regime(
    *,
    adx_strength: str,
    atr_class: str,
    atr_expansion: str,
    structure_label: str,
    session_behavior: str,
    volume_signal: str,
    momentum_label: str,
    weights: dict[str, float] | None = None,
    probability_threshold: float = 0.55,
) -> VoteOutput:
    w = weights or {}
    w_adx = float(w.get("adx", 1.0))
    w_atr = float(w.get("atr", 1.0))
    w_structure = float(w.get("structure", 1.0))
    w_session = float(w.get("session", 1.0))
    w_volume = float(w.get("volume", 1.0))
    w_momentum = float(w.get("momentum", 1.0))
    votes: dict[str, float] = defaultdict(float)

    if adx_strength == "STRONG_TREND":
        votes["TREND_HIGH_VOL"] += 0.5 * w_adx
        votes["PULLBACK_CONTINUATION"] += 0.4 * w_adx
    elif adx_strength == "WEAK_TREND":
        votes["TREND_LOW_VOL"] += 0.6 * w_adx
    elif adx_strength == "NO_TREND":
        votes["RANGE_LOW_VOL"] += 0.6 * w_adx

    if atr_class == "CHAOTIC_VOL":
        votes["NEWS_CHAOS"] += 0.8 * w_atr
    elif atr_class == "HIGH_VOL":
        votes["RANGE_HIGH_VOL"] += 0.4 * w_atr
        votes["BREAKOUT_EXPANSION"] += 0.4 * w_atr
    elif atr_class == "LOW_VOL":
        votes["ASIA_COMPRESSION"] += 0.5 * w_atr

    if atr_expansion == "EXPANDING":
        votes["BREAKOUT_EXPANSION"] += 0.35 * w_atr
    if atr_expansion == "COMPRESSING":
        votes["ASIA_COMPRESSION"] += 0.25 * w_atr

    if structure_label == "BREAKOUT":
        votes["BREAKOUT_EXPANSION"] += 0.8 * w_structure
    elif structure_label == "RANGE":
        votes["RANGE_LOW_VOL"] += 0.4 * w_structure
        votes["LIQUIDITY_SWEEP"] += 0.4 * w_structure
    elif structure_label == "EXHAUSTION":
        votes["EXHAUSTION"] += 0.8 * w_structure
    elif structure_label == "COMPRESSION":
        votes["ASIA_COMPRESSION"] += 0.6 * w_structure

    if session_behavior == "NY_CONTINUATION_REVERSAL":
        votes["NY_REVERSAL"] += 0.4 * w_session
    elif session_behavior == "ASIA_COMPRESSION":
        votes["ASIA_COMPRESSION"] += 0.4 * w_session
    elif session_behavior == "LONDON_EXPANSION":
        votes["BREAKOUT_EXPANSION"] += 0.3 * w_session

    if volume_signal == "SPIKE":
        votes["BREAKOUT_EXPANSION"] += 0.25 * w_volume
    elif volume_signal == "DRY":
        votes["RANGE_LOW_VOL"] += 0.2 * w_volume

    if momentum_label in ("OVERBOUGHT", "OVERSOLD"):
        votes["EXHAUSTION"] += 0.25 * w_momentum
    if momentum_label == "BULLISH_MOMENTUM":
        votes["PULLBACK_CONTINUATION"] += 0.2 * w_momentum
    if momentum_label == "BEARISH_MOMENTUM":
        votes["NY_REVERSAL"] += 0.2 * w_momentum

    if not votes:
        votes["TRANSITION"] += 1.0
    probs = normalize_votes(votes, REGIME_12)
    sorted_items = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    label = sorted_items[0][0]
    conf, uncertainty = confidence_and_uncertainty(probs)
    if conf < probability_threshold:
        label = "TRANSITION"
    return VoteOutput(label, probs, conf, uncertainty, dict(votes))

