"""
Trading session and market-time management system.

This module tracks:
- UTC time
- IST time
- Active trading session
- Weekend status

It can publish this information to Redis every 10 seconds.
"""

from datetime import datetime, time as dt_time
import pytz
import time

from core.logger import get_logger
from core.bus import set_value

log = get_logger()

# Timezones used in this module.
UTC_TZ = pytz.utc
IST_TZ = pytz.timezone("Asia/Kolkata")

# Session boundaries are defined in UTC for consistency.
# Note: These are practical fixed windows for Phase 2 simplicity.
ASIA_START_UTC = dt_time(0, 0)      # 00:00 UTC
ASIA_END_UTC = dt_time(9, 0)        # 09:00 UTC
LONDON_START_UTC = dt_time(8, 0)    # 08:00 UTC
LONDON_END_UTC = dt_time(17, 0)     # 17:00 UTC
NEWYORK_START_UTC = dt_time(13, 0)  # 13:00 UTC
NEWYORK_END_UTC = dt_time(22, 0)    # 22:00 UTC
OVERLAP_START_UTC = dt_time(13, 0)  # 13:00 UTC
OVERLAP_END_UTC = dt_time(17, 0)    # 17:00 UTC


def _utc_now():
    """Internal helper to get timezone-aware UTC datetime."""
    return datetime.now(UTC_TZ)


def _is_in_range(current_time, start_time, end_time):
    """
    Return True if current_time is inside [start_time, end_time).
    Uses half-open intervals to avoid overlap-edge ambiguity.
    """
    return start_time <= current_time < end_time


def get_utc_time():
    """Return current UTC time as readable string."""
    return _utc_now().strftime("%Y-%m-%d %H:%M:%S")


def get_ist_time():
    """Return current IST time as readable string."""
    return _utc_now().astimezone(IST_TZ).strftime("%Y-%m-%d %H:%M:%S")


def is_london_session():
    """Return True when current UTC time is in London session."""
    now_utc_time = _utc_now().time()
    return _is_in_range(now_utc_time, LONDON_START_UTC, LONDON_END_UTC)


def is_newyork_session():
    """Return True when current UTC time is in New York session."""
    now_utc_time = _utc_now().time()
    return _is_in_range(now_utc_time, NEWYORK_START_UTC, NEWYORK_END_UTC)


def is_overlap_session():
    """Return True during London-New York overlap window."""
    now_utc_time = _utc_now().time()
    return _is_in_range(now_utc_time, OVERLAP_START_UTC, OVERLAP_END_UTC)


def is_asia_session():
    """Return True when current UTC time is in Asia session."""
    now_utc_time = _utc_now().time()
    return _is_in_range(now_utc_time, ASIA_START_UTC, ASIA_END_UTC)


def is_weekend():
    """
    Return True on Saturday/Sunday in UTC.
    Forex market is generally closed on weekends.
    """
    weekday = _utc_now().weekday()  # Monday=0 ... Sunday=6
    return weekday >= 5


def get_current_session():
    """
    Return the most relevant current session label.

    Priority:
    1) Weekend
    2) Overlap
    3) London
    4) New York
    5) Asia
    6) Off-session
    """
    if is_weekend():
        return "Weekend"

    if is_overlap_session():
        return "London-New York overlap"

    if is_london_session():
        return "London"

    if is_newyork_session():
        return "New York"

    if is_asia_session():
        return "Asia"

    return "Off-session"


def publish_clock_status():
    """
    Publish one snapshot of clock/session data to Redis.

    Returns:
      True when publish succeeds, otherwise False.
    """
    utc_time = get_utc_time()
    ist_time = get_ist_time()
    session = get_current_session()
    weekend_flag = is_weekend()
    london_flag = is_london_session()
    newyork_flag = is_newyork_session()
    overlap_flag = is_overlap_session()
    asia_flag = is_asia_session()

    try:
        set_value("clock:utc", utc_time)
        set_value("clock:ist", ist_time)
        set_value("clock:session", session)
        set_value("clock:is_weekend", weekend_flag)
        set_value("clock:london", london_flag)
        set_value("clock:newyork", newyork_flag)
        set_value("clock:overlap", overlap_flag)
        set_value("clock:asia", asia_flag)
        set_value("clock:status", "running")

        log.info(
            "clock update | utc=%s | ist=%s | session=%s | weekend=%s | london=%s | newyork=%s | overlap=%s | asia=%s",
            utc_time,
            ist_time,
            session,
            weekend_flag,
            london_flag,
            newyork_flag,
            overlap_flag,
            asia_flag,
        )
        return True
    except Exception as exc:
        # Graceful handling: keep loop alive and mark status as error.
        try:
            set_value("clock:status", "error")
        except Exception:
            pass
        log.error(f"publish_clock_status failed: {exc}")
        return False


def run_clock():
    """
    Continuous publisher loop.

    Publishes clock/session updates every 10 seconds.
    """
    log.info("Clock engine started. Publishing every 10 seconds.")
    try:
        set_value("clock:status", "starting")
    except Exception as exc:
        log.warning(f"Unable to publish clock starting status: {exc}")

    try:
        while True:
            publish_clock_status()
            time.sleep(10)
    except KeyboardInterrupt:
        try:
            set_value("clock:status", "stopped")
        except Exception:
            pass
        log.info("Clock engine stopped by user (KeyboardInterrupt).")
    except Exception as exc:
        try:
            set_value("clock:status", "error")
        except Exception:
            pass
        log.error(f"Clock engine crashed: {exc}")


if __name__ == "__main__":
    run_clock()

