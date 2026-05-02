from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class RegimeLabel(Enum):
    TREND = "trend"
    RANGE = "range"
    TRANSITION = "transition"
    UNKNOWN = "unknown"


@dataclass
class RegimeState:
    label: RegimeLabel
    confidence: float
    components: dict[str, float]
    time_utc: datetime

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["time_utc"] = self.time_utc.isoformat()
        d["label"] = self.label.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RegimeState:
        t = dict(d)
        t["time_utc"] = datetime.fromisoformat(str(t["time_utc"]).replace("Z", "+00:00"))
        t["label"] = RegimeLabel(t["label"])
        return cls(**t)
