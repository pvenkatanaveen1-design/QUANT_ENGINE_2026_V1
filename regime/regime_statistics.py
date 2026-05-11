from __future__ import annotations

from collections import Counter


def transition_frequencies(rows: list[dict]) -> dict[str, int]:
    transitions: Counter[str] = Counter()
    for r in rows:
        t = str(r.get("transition_state") or "UNKNOWN")
        transitions[t] += 1
    return dict(transitions)


def regime_occurrences(rows: list[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for r in rows:
        key = str(r.get("regime_label") or r.get("regime") or "UNKNOWN")
        counts[key] += 1
    return dict(counts)

