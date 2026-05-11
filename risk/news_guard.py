"""Simulated high-impact news blackout guard — Redis only (no Forex Factory / APIs yet)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from core.bus import set_value
from core.config import load_config
from core.logger import get_logger

log = get_logger()

# How often we refresh news:* keys (seconds).
NEWS_GUARD_INTERVAL_SECONDS = 5

# Minutes before / after the event print time that count as blackout (prop-style caution band).
BLACKOUT_BEFORE_MINUTES = 15
BLACKOUT_AFTER_MINUTES = 15


def _slot_today_or_tomorrow(now_utc: datetime, hour: int, minute: int) -> datetime:
    """Return the next occurrence of hour:minute UTC on or after now_utc (same calendar algorithm daily)."""
    d = now_utc.date()
    candidate = datetime(d.year, d.month, d.day, hour, minute, 0, tzinfo=timezone.utc)
    if candidate <= now_utc:
        candidate += timedelta(days=1)
    return candidate


def load_test_news_events(now_utc: datetime | None = None) -> list[dict]:
    """
    Simulated recurring high-impact events (UTC clock times).

    Replace this with API-fed events later; execution layers should only read Redis `news:*`.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    schedule = [
        (13, 30, "US NFP (simulated)"),
        (15, 0, "US CPI (simulated)"),
        (19, 0, "FOMC (simulated)"),
    ]
    events = []
    for hour, minute, name in schedule:
        events.append(
            {
                "name": name,
                "event_time": _slot_today_or_tomorrow(now_utc, hour, minute),
            }
        )
    return sorted(events, key=lambda x: x["event_time"])


def calculate_minutes_to_event(event_time: datetime, now_utc: datetime) -> float:
    """Signed minutes until `event_time` (negative if the timestamp is in the past)."""
    return (event_time - now_utc).total_seconds() / 60.0


def is_blackout_active(
    now_utc: datetime,
    event_time: datetime,
    before_minutes: int = BLACKOUT_BEFORE_MINUTES,
    after_minutes: int = BLACKOUT_AFTER_MINUTES,
) -> bool:
    """True when now lies in [event - before, event + after] (all UTC-aware)."""
    start = event_time - timedelta(minutes=before_minutes)
    end = event_time + timedelta(minutes=after_minutes)
    return start <= now_utc <= end


def _compute_news_fields(events: list[dict], now_utc: datetime) -> dict:
    """Build the Redis payload for the current simulation tick."""
    if not events:
        return {
            "news:event": "",
            "news:event_time": "",
            "news:blackout": "INACTIVE",
            "news:blackout_reason": "No simulated events configured",
            "news:minutes_remaining": None,
            "news:status": "ERROR",
        }

    active: dict | None = None
    for ev in sorted(events, key=lambda x: x["event_time"]):
        if is_blackout_active(now_utc, ev["event_time"]):
            active = ev
            break

    if active is not None:
        end = active["event_time"] + timedelta(minutes=BLACKOUT_AFTER_MINUTES)
        mins_remaining = max(0.0, (end - now_utc).total_seconds() / 60.0)
        return {
            "news:event": active["name"],
            "news:event_time": active["event_time"].isoformat(),
            "news:blackout": "ACTIVE",
            "news:blackout_reason": (
                f"High-impact window: {BLACKOUT_BEFORE_MINUTES}m before / "
                f"{BLACKOUT_AFTER_MINUTES}m after {active['name']}"
            ),
            "news:minutes_remaining": round(mins_remaining, 2),
            "news:status": "RUNNING",
        }

    next_ev = None
    next_blackout_start: datetime | None = None
    for ev in sorted(events, key=lambda x: x["event_time"]):
        bs = ev["event_time"] - timedelta(minutes=BLACKOUT_BEFORE_MINUTES)
        if bs > now_utc:
            if next_blackout_start is None or bs < next_blackout_start:
                next_blackout_start = bs
                next_ev = ev

    upcoming = min(events, key=lambda x: x["event_time"])
    mins_to_blackout: float | None = None
    if next_blackout_start is not None:
        mins_to_blackout = max(0.0, (next_blackout_start - now_utc).total_seconds() / 60.0)

    reason = "No simulated news window active"
    if next_ev is not None and mins_to_blackout is not None:
        reason = f"Next window starts in ~{mins_to_blackout:.1f} min ({next_ev['name']})"

    return {
        "news:event": upcoming["name"],
        "news:event_time": upcoming["event_time"].isoformat(),
        "news:blackout": "INACTIVE",
        "news:blackout_reason": reason,
        "news:minutes_remaining": round(mins_to_blackout, 2) if mins_to_blackout is not None else None,
        "news:status": "RUNNING",
    }


def publish_news_status(fields: dict) -> None:
    """Write news:* keys plus `news:last_update` (Unix timestamp, UTC)."""
    now_ts = datetime.now(timezone.utc).timestamp()
    payload = dict(fields)
    payload["news:last_update"] = now_ts

    for key, value in payload.items():
        if not str(key).startswith("news:"):
            continue
        try:
            set_value(str(key), value)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"news_guard | set_value failed key={key} err={exc}")

    log.info(
        f"news_guard | publish | blackout={payload.get('news:blackout')} "
        f"event={payload.get('news:event')} mins={payload.get('news:minutes_remaining')}"
    )


def evaluate_news_guard_once(
    now_utc: datetime | None = None,
    events: list[dict] | None = None,
) -> dict:
    """
    One tick: compute fields, publish Redis.

    Tests can pass frozen `now_utc` and a custom `events` list.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if events is None:
        events = load_test_news_events(now_utc)
    fields = _compute_news_fields(events, now_utc)
    publish_news_status(fields)
    return fields


def run_news_guard() -> None:
    """Daemon loop (≈5 s): refresh simulated calendar → Redis."""
    cfg = load_config()
    firm = cfg.get("PROP_FIRM_NAME", "Unknown")
    log.info(
        f"news_guard | daemon start | interval_s={NEWS_GUARD_INTERVAL_SECONDS} | prop_label={firm}"
    )
    while True:
        try:
            evaluate_news_guard_once()
        except Exception:  # noqa: BLE001
            log.exception("news_guard | tick failed")
        time.sleep(NEWS_GUARD_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_news_guard()