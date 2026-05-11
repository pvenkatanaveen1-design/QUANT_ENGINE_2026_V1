from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass
class Tick:
    symbol: str
    bid: float
    ask: float
    time_utc: datetime
    last: float | None = None
    volume: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["time_utc"] = self.time_utc.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Tick:
        t = dict(d)
        tc = t.get("time_utc")
        if isinstance(tc, str):
            t["time_utc"] = datetime.fromisoformat(tc.replace("Z", "+00:00"))
        return cls(**t)
