from __future__ import annotations

from schemas.order import Order


def fanout_duplicate(_leader: Order) -> None:
    """Phase 4: broadcast same signal/order to mirrored accounts safely."""
    return
