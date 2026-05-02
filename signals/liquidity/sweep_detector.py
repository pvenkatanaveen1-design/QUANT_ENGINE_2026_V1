from __future__ import annotations


def liquidity_sweep(side: str, last_mid: float, level: float | None, buffer: float = 0.0) -> bool:
    if level is None:
        return False
    if side.lower() == "buy":
        return last_mid >= level - buffer
    if side.lower() == "sell":
        return last_mid <= level + buffer
    return False
