from __future__ import annotations


def passes_news_gate(_symbol: str) -> bool:
    """Phase 1: Forexfactory blackout. Phase 0: always passes."""
    return True
