from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SignalStrength(Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


@dataclass
class Signal:
    symbol: str
    direction: str
    rationale: str
    strength: SignalStrength
    time_utc: datetime
    confluence_score: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["time_utc"] = self.time_utc.isoformat()
        d["strength"] = self.strength.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Signal:
        t = dict(d)
        t["time_utc"] = datetime.fromisoformat(str(t["time_utc"]).replace("Z", "+00:00"))
        if isinstance(t.get("strength"), str):
            t["strength"] = SignalStrength(t["strength"])
        if t.get("extras") is None:
            t["extras"] = {}
        return cls(
            symbol=t["symbol"],
            direction=t["direction"],
            rationale=t["rationale"],
            strength=t["strength"],
            time_utc=t["time_utc"],
            confluence_score=float(t.get("confluence_score", 0.0)),
            extras=dict(t.get("extras", {})),
        )
