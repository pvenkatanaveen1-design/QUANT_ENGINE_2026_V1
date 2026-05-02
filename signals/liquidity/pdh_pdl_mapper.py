from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RangeLevels:
    prior_day_high: float | None
    prior_day_low: float | None


def prior_range_from_mids(
    mids: deque[float] | list[float],
    window: int,
) -> RangeLevels:
    """window = approximate number of mids that map to one trading day."""
    if len(mids) < window * 2:
        return RangeLevels(None, None)
    arr = list(mids)
    yesterday = arr[-(2 * window) : -window]
    return RangeLevels(max(yesterday), min(yesterday))
