from __future__ import annotations

from datetime import datetime

from features.session_flags import is_within_london_ny_window


def passes_session(now_utc: datetime) -> bool:
    return is_within_london_ny_window(now_utc)
