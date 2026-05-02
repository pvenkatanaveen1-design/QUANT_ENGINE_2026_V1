from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    REJECTED = "rejected"
    CLOSED = "closed"


@dataclass
class Order:
    symbol: str
    side: OrderSide
    volume_lots: float
    sl_price: float | None
    tp_price: float | None
    magic: int
    comment: str
    time_utc: datetime
    status: OrderStatus = OrderStatus.PENDING
    broker_ticket: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["time_utc"] = self.time_utc.isoformat()
        d["side"] = self.side.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Order:
        t = dict(d)
        t["time_utc"] = datetime.fromisoformat(str(t["time_utc"]).replace("Z", "+00:00"))
        t["side"] = OrderSide(t["side"])
        t["status"] = OrderStatus(t["status"])
        return cls(**t)
