from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


@dataclass
class SessionClassification:
    session_label: str
    behavior_label: str


def _to_ist(dt: datetime | None = None) -> datetime:
    now_utc = dt or datetime.now(timezone.utc)
    return now_utc.astimezone(timezone(timedelta(hours=5, minutes=30)))


def classify_session(
    now: datetime | None = None,
    *,
    asia_start_hour_ist: float = 5.5,
    london_start_hour_ist: float = 12.5,
    newyork_start_hour_ist: float = 18.5,
    dst_offset_hours: float = 0.0,
) -> SessionClassification:
    t = _to_ist(now)
    h = t.hour + t.minute / 60.0 + dst_offset_hours
    if asia_start_hour_ist <= h < london_start_hour_ist:
        return SessionClassification("ASIA", "ASIA_COMPRESSION")
    if london_start_hour_ist <= h < newyork_start_hour_ist:
        return SessionClassification("LONDON", "LONDON_EXPANSION")
    if newyork_start_hour_ist <= h < 23.5:
        return SessionClassification("NEW_YORK", "NY_CONTINUATION_REVERSAL")
    return SessionClassification("OFF", "LOW_LIQUIDITY")

