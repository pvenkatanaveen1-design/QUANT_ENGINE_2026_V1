from __future__ import annotations

from typing import Any


def sanitize_tick_dict(tick: dict[str, Any]) -> dict[str, Any]:
    """Phase 1 will add spike filtering — pass-through today."""
    return dict(tick)
