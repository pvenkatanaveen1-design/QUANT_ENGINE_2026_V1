from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FillRecord:
    broker_ticket: int
    symbol: str
    side: str
    volume_lots: float
    price: float
    sl_price: float | None
    tp_price: float | None
    commission: float
    swap: float
    magic: int
    comment: str


@dataclass
class ClosedTradeSummary:
    symbol: str
    profit: float
    volume_lots: float
    commission: float
    swap: float
