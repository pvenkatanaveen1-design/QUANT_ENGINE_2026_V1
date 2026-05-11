from __future__ import annotations

from datetime import datetime, timezone

LONDON_OPEN_UTC_HOUR = 7
NEW_YORK_CLOSE_UTC_HOUR = 20


def utc_hour(dt: datetime) -> int:
    return dt.astimezone(timezone.utc).hour


def is_within_london_ny_window(dt: datetime) -> bool:
    h = utc_hour(dt)
    return LONDON_OPEN_UTC_HOUR <= h <= NEW_YORK_CLOSE_UTC_HOUR
