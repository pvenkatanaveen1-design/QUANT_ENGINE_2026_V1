"""Demo scenarios for `risk/news_guard.py` (simulated news blackouts).

Requires Redis. From project root:

    python scripts/test_news_guard.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.bus import get_value
from risk.news_guard import evaluate_news_guard_once


def print_news_snapshot(title: str) -> None:
    print("")
    print("==============================================")
    print(title)
    print("==============================================")
    ev = get_value("news:event")
    ev_t = get_value("news:event_time")
    bo = get_value("news:blackout")
    reason = get_value("news:blackout_reason")
    mins = get_value("news:minutes_remaining")
    upd = get_value("news:last_update")
    st = get_value("news:status")
    print(f"  news:event               = {ev}")
    print(f"  news:event_time          = {ev_t}")
    print(f"  news:blackout            = {bo}")
    print(f"  news:blackout_reason     = {reason}")
    print(f"  news:minutes_remaining   = {mins}")
    print(f"  news:last_update         = {upd}")
    print(f"  news:status              = {st}")


def main() -> None:
    now = datetime.now(timezone.utc)

    # Scenario A — inside blackout: event "now" sits in the center of the ±15m window.
    print_news_snapshot("Scenario A — expect ACTIVE blackout (event at current UTC time)")
    evaluate_news_guard_once(
        now_utc=now,
        events=[{"name": "US NFP (simulated)", "event_time": now}],
    )
    print_news_snapshot("After publish — Scenario A")

    # Scenario B — far before window: blackout should be INACTIVE.
    print_news_snapshot("Scenario B — expect INACTIVE (event +45 minutes)")
    future = now + timedelta(minutes=45)
    evaluate_news_guard_once(
        now_utc=now,
        events=[{"name": "US CPI (simulated)", "event_time": future}],
    )
    print_news_snapshot("After publish — Scenario B")

    # Scenario C — default rolling test events (NFP / CPI / FOMC slots).
    print_news_snapshot("Scenario C — default load_test_news_events() schedule")
    evaluate_news_guard_once()
    print_news_snapshot("After publish — Scenario C")

    print("")
    print("Done. Open dashboard NEWS GUARD STATUS or inspect Redis keys news:*.")


if __name__ == "__main__":
    main()
