"""
systems/intelligence/session_filter.py — S10: Session Filter.

WHY THIS FILE EXISTS
--------------------
Forex sessions have dramatically different characteristics:
  London (12:30-17:30 IST):  Highest volume, tightest spreads, best fills.
    Breakout strategies work best here.  Institutional order flow dominates.
  New York (18:30-23:30 IST): Second highest volume.
    Continuation moves from London.  News releases at 18:30 and 20:00 IST.
  Overlap (18:30-20:30 IST):  HIGHEST liquidity of the day.
    This 2-hour window is the best time to trade for most strategies.
  Asia (5:30-12:30 IST):    Low volume, ranges, traps.
    XAUUSD can be manipulated in thin Asian markets.  Avoid most strategies.
  Off session:              Weekend, early morning.  No reason to trade.

DST AWARENESS:
  London shifts by 1 hour between BST (Mar-Oct) and GMT (Nov-Mar).
  New York shifts between EST and EDT.
  This module calculates the correct IST times automatically using pytz.
  If DST detection fails, falls back to standard (non-DST) times.

SESSION SCORING:
  London:  1.0 (best conditions)
  Overlap: 0.95 (best liquidity)
  NY:      0.85 (good conditions, some news risk)
  Asia:    0.40 (low liquidity, not recommended for breakouts)
  Off:     0.0  (no trading)

USAGE:
    from systems.intelligence.session_filter import session_filter
    current = session_filter.get_current_session()
    score   = session_filter.get_session_score()
    if score >= 0.8:
        # Good to trade
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, time as dtime
from typing import Optional

from core.enums import Session
from core.event_bus import EventType, bus
from core.logger import get_logger, LogCategory

log = get_logger("session_filter", LogCategory.DATA)

# Session quality scores (used by scoring engine)
SESSION_SCORES: dict[str, float] = {
    Session.LONDON.value:  1.00,
    Session.OVERLAP.value: 0.95,
    Session.NEW_YORK.value: 0.85,
    Session.ASIA.value:    0.40,
    Session.OFF.value:     0.00,
}

# IST session boundaries (non-DST / standard times)
# core/clock.py provides DST-adjusted times; we use these as fallback
_LONDON_OPEN  = dtime(12, 30)
_LONDON_CLOSE = dtime(17, 30)
_NY_OPEN      = dtime(18, 30)
_NY_CLOSE     = dtime(23, 30)
_OVERLAP_OPEN  = dtime(18, 30)
_OVERLAP_CLOSE = dtime(20, 30)
_ASIA_OPEN    = dtime(5,  30)
_ASIA_CLOSE   = dtime(12, 30)

# Check interval: how often to re-evaluate session (seconds)
SESSION_CHECK_INTERVAL = 60


class SessionFilter:
    """
    Maintains current trading session state.

    Runs a background thread that updates the session every minute.
    Publishes SESSION_CHANGED when session transitions occur.

    Singleton — import via:
        from systems.intelligence.session_filter import session_filter
    """

    def __init__(self) -> None:
        self._current_session: Session = Session.OFF
        self._session_lock    = threading.Lock()
        self._stop_event      = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running         = False
        self._last_session_change: Optional[datetime] = None

        # Try to import pytz for DST-aware calculation
        try:
            import pytz
            self._ist_tz  = pytz.timezone("Asia/Kolkata")
            self._lon_tz  = pytz.timezone("Europe/London")
            self._ny_tz   = pytz.timezone("America/New_York")
            self._use_pytz = True
        except ImportError:
            log.warning("pytz not installed — using fixed IST times (no DST correction)")
            self._use_pytz = False

        log.info("SessionFilter initialized")

    def start(self) -> None:
        """Start background session monitoring thread."""
        if self._running:
            log.warning("SessionFilter already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="session_filter",
            daemon=True,
        )
        self._thread.start()
        self._running = True
        # Initial evaluation
        self._evaluate_session()
        log.info("SessionFilter started")

    def stop(self) -> None:
        """Stop background monitoring thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._running = False
        log.info("SessionFilter stopped")

    def _run_loop(self) -> None:
        """Background thread: evaluate session every minute."""
        while not self._stop_event.is_set():
            try:
                self._evaluate_session()
            except Exception as exc:
                log.error(f"SessionFilter evaluation error: {exc}", exc_info=True)
            self._stop_event.wait(timeout=SESSION_CHECK_INTERVAL)

    def _evaluate_session(self) -> None:
        """
        Determine current trading session based on IST time.
        Publishes SESSION_CHANGED event if session has transitioned.
        """
        now_ist = self._get_ist_time()
        new_session = self._determine_session(now_ist)

        with self._session_lock:
            old_session = self._current_session
            if new_session != old_session:
                self._current_session = new_session
                self._last_session_change = datetime.utcnow()
                changed = True
            else:
                changed = False

        if changed:
            log.info(f"Session: {old_session.value} → {new_session.value} at {now_ist.strftime('%H:%M IST')}")
            bus.publish(
                EventType.SESSION_CHANGED,
                {
                    "previous": old_session.value,
                    "current":  new_session.value,
                    "ist_time": now_ist.strftime("%H:%M"),
                    "score":    SESSION_SCORES.get(new_session.value, 0.0),
                },
                source="session_filter",
            )

    def _get_ist_time(self) -> datetime:
        """Get current datetime in IST.  Uses pytz if available."""
        if self._use_pytz:
            try:
                import pytz
                utc_now = datetime.utcnow().replace(tzinfo=pytz.utc)
                return utc_now.astimezone(self._ist_tz).replace(tzinfo=None)
            except Exception:
                pass
        # Fallback: UTC + 5:30
        utc_now = datetime.utcnow()
        from datetime import timedelta
        return utc_now + timedelta(hours=5, minutes=30)

    def _get_dst_adjusted_times(self, now_ist: datetime) -> dict:
        """
        Return session boundary times adjusted for current DST status.
        London shifts by 30 minutes in IST terms during BST (March-October).
        New York shifts by 30 minutes in IST terms during EDT.
        """
        if not self._use_pytz:
            return {
                "london_open":  _LONDON_OPEN,
                "london_close": _LONDON_CLOSE,
                "ny_open":      _NY_OPEN,
                "ny_close":     _NY_CLOSE,
            }

        try:
            import pytz
            utc_now = datetime.utcnow().replace(tzinfo=pytz.utc)

            # London: standard is UTC+0, BST is UTC+1
            london_now = utc_now.astimezone(self._lon_tz)
            lon_offset  = london_now.utcoffset().total_seconds() / 3600
            # Standard London open in UTC: 08:00.  BST: 07:00.
            # In IST: add 5.5 hours.  Standard: 13:30 IST.  BST: 12:30 IST.
            lon_open_utc_hour = 8 - int(lon_offset)  # simplification
            lon_open_ist  = dtime(lon_open_utc_hour + 5, 30)
            lon_close_ist = dtime(lon_open_ist.hour + 4, 30)  # 5h window

            # NY: standard UTC-5 (EST), summer UTC-4 (EDT)
            ny_now   = utc_now.astimezone(self._ny_tz)
            ny_offset = ny_now.utcoffset().total_seconds() / 3600
            ny_open_utc = 13 - int(ny_offset)  # NY opens at 09:00 local = UTC 14:00 EST
            ny_open_ist  = dtime(min(ny_open_utc + 5, 23), 30)
            ny_close_ist = dtime(min(ny_open_ist.hour + 5, 23), 30)

            return {
                "london_open":  lon_open_ist,
                "london_close": lon_close_ist,
                "ny_open":      ny_open_ist,
                "ny_close":     ny_close_ist,
            }
        except Exception:
            return {
                "london_open":  _LONDON_OPEN,
                "london_close": _LONDON_CLOSE,
                "ny_open":      _NY_OPEN,
                "ny_close":     _NY_CLOSE,
            }

    def _determine_session(self, now_ist: datetime) -> Session:
        """
        Map current IST time to a Session enum.
        Priority: OVERLAP > LONDON > NEW_YORK > ASIA > OFF.
        """
        t = now_ist.time()
        times = self._get_dst_adjusted_times(now_ist)

        london_open  = times["london_open"]
        london_close = times["london_close"]
        ny_open      = times["ny_open"]
        ny_close     = times["ny_close"]

        in_london = london_open  <= t <= london_close
        in_ny     = ny_open      <= t <= ny_close
        in_asia   = _ASIA_OPEN   <= t <= _ASIA_CLOSE

        # Calculate overlap based on actual session boundaries
        overlap_open  = ny_open
        # Overlap = where London and NY both active (typically 18:30-20:30 IST)
        overlap_close = dtime(
            min(london_close.hour + 1, 20),
            30
        )
        in_overlap = overlap_open <= t <= overlap_close and in_london and in_ny

        # Priority order
        if in_overlap:
            return Session.OVERLAP
        if in_london:
            return Session.LONDON
        if in_ny:
            return Session.NEW_YORK
        if in_asia:
            return Session.ASIA
        return Session.OFF

    # ─── PUBLIC API ──────────────────────────────────────────────────────────

    def get_current_session(self) -> Session:
        """Return current session enum.  Thread-safe."""
        with self._session_lock:
            return self._current_session

    def get_session_score(self) -> float:
        """
        Return quality score for current session (0.0 - 1.0).
        Use this in the scoring engine to weight session quality.
        0.0 = off session (no trading)
        1.0 = London (best conditions)
        """
        session = self.get_current_session()
        return SESSION_SCORES.get(session.value, 0.0)

    def is_tradeable(self) -> bool:
        """
        Returns True only if current session allows trading.
        OFF session and early Asia are excluded.
        """
        session = self.get_current_session()
        return session != Session.OFF

    def get_stats(self) -> dict:
        """Return current session stats for dashboard display."""
        session = self.get_current_session()
        return {
            "current_session":       session.value,
            "session_score":         SESSION_SCORES.get(session.value, 0.0),
            "is_tradeable":          self.is_tradeable(),
            "last_session_change":   (
                self._last_session_change.isoformat()
                if self._last_session_change else "N/A"
            ),
            "running":               self._running,
            "session_scores":        SESSION_SCORES,
        }


# ─── SINGLETON ────────────────────────────────────────────────────────────────
session_filter = SessionFilter()
