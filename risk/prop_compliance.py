from __future__ import annotations


def enforced_min_rr(_rr_ratio: float) -> bool:
    """Phase 1+: FTMO RR + duration tagging."""
    return _rr_ratio >= 1.0
