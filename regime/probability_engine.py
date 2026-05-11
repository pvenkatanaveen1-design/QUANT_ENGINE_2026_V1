from __future__ import annotations


def normalize_votes(votes: dict[str, float], states: list[str]) -> dict[str, float]:
    total = sum(max(0.0, v) for v in votes.values())
    if total <= 0:
        base = 1.0 / max(1, len(states))
        return {s: round(base, 4) for s in states}
    out = {k: round(max(0.0, votes.get(k, 0.0)) / total, 4) for k in states}
    return out


def confidence_and_uncertainty(probabilities: dict[str, float]) -> tuple[float, float]:
    sorted_probs = sorted(probabilities.values(), reverse=True)
    top = sorted_probs[0] if sorted_probs else 0.0
    nxt = sorted_probs[1] if len(sorted_probs) > 1 else 0.0
    confidence = max(0.0, min(1.0, top - nxt + 0.5))
    uncertainty = round(1.0 - top, 4)
    return round(confidence, 2), uncertainty

