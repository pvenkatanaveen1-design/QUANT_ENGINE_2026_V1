from __future__ import annotations

from collections import deque


def asian_hi_lo(
    highs: deque[float] | list[float],
    lows: deque[float] | list[float],
    asian_len: int,
) -> tuple[float | None, float | None]:
    if len(highs) < asian_len:
        return None, None
    h = list(highs)[-asian_len:]
    l = list(lows)[-asian_len:]
    return max(h), min(l)
