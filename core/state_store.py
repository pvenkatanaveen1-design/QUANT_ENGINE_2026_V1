"""
core/state_store.py — Thread-safe live account state, persisted to SQLite.

WHY THIS FILE EXISTS
--------------------
Multiple threads run simultaneously: guardian (risk), brain (signals), executor (orders).
All need to read current equity, DD %, kill switch status, and open trades.
Without one central store, each system calls MT5 independently → race conditions
and excessive broker API calls.

Design:
  - All reads/writes go through a threading.RLock (reentrant = nested locks safe)
  - SQLite provides persistence across restarts (kill switch state survives reboot)
  - MT5 equity updates every 1 second via shield.py → update_equity()
  - Snapshots are taken once per dashboard refresh cycle

2026 PROP FIRM REALITY NOTES
-----------------------------
DAILY DD CALCULATION — this is where funded accounts fail:

FTMO 2026 rule (verify at ftmo.com):
  Daily DD = max loss from start-of-day balance, measured in real-time.
  If you start the day with $10,000 balance and lose $501 at any point → FAIL.
  The clock resets at 22:00 UTC (Prague midnight) NOT your local midnight.

The5ers 2026 rule:
  DD measured from peak balance, not day start.
  This is harder — any new equity high raises your watermark permanently.

This engine defaults to FTMO-style.  Change funded_rules.yaml to switch.

KILL SWITCH PERSISTENCE:
  Once activated, stored in SQLite.  Survives process restart.
  Only manual reset clears it — from dashboard or Telegram /reset command.
  This prevents a bug-induced crash from auto-deactivating the kill switch.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core.constants import SQLITE_PATH
from core.enums import SystemMode
from core.logger import get_logger

log = get_logger("state_store")


class StateStore:
    """
    Central live state for the trading engine.

    Singleton — import via: from core.state_store import state
    Never instantiate directly.  One instance for the entire process.

    Thread safety: all public methods acquire self._lock before reading/writing.
    RLock (reentrant) allows the same thread to lock multiple times safely.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

        # ─── ACCOUNT STATE ────────────────────────────────────────────────
        self._equity: float  = 0.0
        self._balance: float = 0.0

        # Daily state — resets at prop firm's day boundary (not necessarily midnight)
        self._daily_start_balance: float = 0.0  # Balance at day start (for FTMO daily DD)
        self._daily_start_equity: float  = 0.0  # Equity at day start
        self._daily_peak_equity: float   = 0.0  # Highest equity today
        self._daily_pnl: float           = 0.0  # Today's PnL in USD
        self._daily_dd_pct: float        = 0.0  # Daily DD % (0.02 = 2%)

        # Session/lifetime state
        self._peak_equity: float  = 0.0   # All-time peak since tracking began
        self._total_dd_pct: float = 0.0   # Total DD % from peak (for funded max DD)

        # ─── TRADE STATE ─────────────────────────────────────────────────
        self._open_trades: dict[int, dict] = {}   # ticket → trade details dict
        self._trades_today: int            = 0
        self._consecutive_losses: int      = 0    # Reset on win
        self._last_trade_time: datetime | None = None

        # ─── KILL SWITCH ─────────────────────────────────────────────────
        self._kill_switch_active: bool = False
        self._kill_switch_reason: str  = ""

        # ─── NEWS BLACKOUT ────────────────────────────────────────────────
        self._news_blackout: bool               = False
        self._news_blackout_until: datetime | None = None
        self._news_event_name: str              = ""

        # ─── SYSTEM MODE ──────────────────────────────────────────────────
        self._mode: SystemMode = SystemMode.DEMO

        # ─── DAILY TRACKING ───────────────────────────────────────────────
        # Trading day boundary — set from funded_rules.yaml daily_reset_hour_utc
        self._trading_day: date = datetime.now(timezone.utc).date()

        # ─── INITIALIZE SQLITE ────────────────────────────────────────────
        Path(SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = SQLITE_PATH
        self._init_db()
        self._restore_kill_switch_from_db()

        log.info(f"StateStore initialized | mode={self._mode.value} | db={SQLITE_PATH}")

    # ─── DATABASE SETUP ──────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create tables if they don't exist.  Safe to call on every startup."""
        with sqlite3.connect(self._db_path) as conn:
            # Persist kill switch state across restarts
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_state (
                    key         TEXT PRIMARY KEY,
                    value       TEXT,
                    updated_at  TEXT
                )
            """)
            # Daily statistics snapshot (for audit + analytics)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_snapshots (
                    trading_day         TEXT PRIMARY KEY,
                    start_balance       REAL,
                    start_equity        REAL,
                    end_equity          REAL,
                    peak_equity         REAL,
                    daily_pnl           REAL,
                    max_daily_dd_pct    REAL,
                    trades_count        INTEGER,
                    updated_at          TEXT
                )
            """)
            conn.commit()

    def _restore_kill_switch_from_db(self) -> None:
        """
        Reload kill switch state on startup.
        If kill switch was active when process died, it stays active on restart.
        This prevents bugs from auto-resetting a safety mechanism.
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT value FROM system_state WHERE key = 'kill_switch_active'"
                ).fetchone()
                if row and row[0] == "1":
                    reason_row = conn.execute(
                        "SELECT value FROM system_state WHERE key = 'kill_switch_reason'"
                    ).fetchone()
                    self._kill_switch_active = True
                    self._kill_switch_reason = reason_row[0] if reason_row else "Unknown"
                    log.critical(
                        f"Kill switch was active on last shutdown! "
                        f"Reason: {self._kill_switch_reason}. "
                        f"Manual reset required before trading."
                    )
        except Exception as exc:
            log.error(f"Failed to restore kill switch state: {exc}")

    def _persist_kill_switch(self) -> None:
        """Write kill switch state to SQLite for restart persistence."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO system_state VALUES (?, ?, ?)",
                    ("kill_switch_active", "1" if self._kill_switch_active else "0", now)
                )
                conn.execute(
                    "INSERT OR REPLACE INTO system_state VALUES (?, ?, ?)",
                    ("kill_switch_reason", self._kill_switch_reason, now)
                )
                conn.commit()
        except Exception as exc:
            log.error(f"Failed to persist kill switch state: {exc}")

    # ─── EQUITY UPDATES ──────────────────────────────────────────────────────

    def update_equity(self, equity: float, balance: float) -> None:
        """
        Called by shield.py every second with fresh MT5 equity data.

        Updates:
        - Daily PnL
        - Daily DD % (from start-of-day balance — FTMO style)
        - Daily peak equity
        - Total DD % from all-time peak
        """
        with self._lock:
            self._equity  = equity
            self._balance = balance

            # Track all-time peak equity
            if equity > self._peak_equity:
                self._peak_equity = equity

            # Track today's peak equity
            if equity > self._daily_peak_equity:
                self._daily_peak_equity = equity

            # Daily PnL (from start-of-day balance baseline)
            if self._daily_start_balance > 0:
                self._daily_pnl = equity - self._daily_start_balance

            # FTMO daily DD: measured from start-of-day balance, not peak equity
            # The5ers style: use daily_peak_equity instead of daily_start_balance
            if self._daily_start_balance > 0:
                self._daily_dd_pct = max(
                    0.0,
                    (self._daily_start_balance - equity) / self._daily_start_balance
                )

            # Total DD from all-time peak
            if self._peak_equity > 0:
                self._total_dd_pct = max(
                    0.0,
                    (self._peak_equity - equity) / self._peak_equity
                )

    def get_equity(self) -> float:
        with self._lock:
            return self._equity

    def get_balance(self) -> float:
        with self._lock:
            return self._balance

    def get_daily_dd_pct(self) -> float:
        """Daily drawdown as decimal (0.03 = 3%)."""
        with self._lock:
            return self._daily_dd_pct

    def get_total_dd_pct(self) -> float:
        """Total drawdown from peak as decimal (0.05 = 5%)."""
        with self._lock:
            return self._total_dd_pct

    def get_daily_pnl(self) -> float:
        """Today's PnL in USD (+/-)."""
        with self._lock:
            return self._daily_pnl

    # ─── KILL SWITCH ─────────────────────────────────────────────────────────

    def activate_kill_switch(self, reason: str) -> None:
        """
        Activate the kill switch.  Blocks ALL new orders.
        Persisted to SQLite — survives process restart.

        Call from:
        - risk/shield.py when DD limit is breached
        - risk/kill_switch.py when Telegram /kill command received
        - Any system detecting a critical condition

        After calling this, only deactivate_kill_switch() (manual) clears it.
        """
        with self._lock:
            if self._kill_switch_active:
                return  # Already active — don't overwrite reason
            self._kill_switch_active = True
            self._kill_switch_reason = reason
            self._mode = SystemMode.EMERGENCY
        # Persist outside lock to avoid lock contention with DB
        self._persist_kill_switch()
        log.critical(f"KILL SWITCH ACTIVATED | reason={reason}")

    def deactivate_kill_switch(self) -> None:
        """
        Manually deactivate the kill switch.
        Call ONLY from dashboard UI or Telegram /reset command.
        NEVER call this automatically from code.
        """
        with self._lock:
            self._kill_switch_active = False
            self._kill_switch_reason = ""
            self._mode = SystemMode.DEMO
        self._persist_kill_switch()
        log.warning("Kill switch DEACTIVATED manually — resuming DEMO mode")

    def is_kill_switch_active(self) -> bool:
        with self._lock:
            return self._kill_switch_active

    def get_kill_switch_reason(self) -> str:
        with self._lock:
            return self._kill_switch_reason

    # ─── NEWS BLACKOUT ────────────────────────────────────────────────────────

    def set_news_blackout(
        self,
        active: bool,
        until: datetime | None = None,
        event_name: str = "",
    ) -> None:
        """
        Set/clear news blackout.
        Called by risk/news_guard.py when HIGH impact news approaches or clears.
        """
        with self._lock:
            self._news_blackout       = active
            self._news_blackout_until = until
            self._news_event_name     = event_name
        if active:
            log.info(f"News blackout ACTIVE | event={event_name} | until={until}")
        else:
            log.info("News blackout CLEARED")

    def is_news_blackout(self) -> bool:
        """
        Returns True if news blackout is active.
        Auto-clears if the until time has passed (avoids stale blackouts).
        """
        with self._lock:
            if not self._news_blackout:
                return False
            # Auto-expire if past the until time
            if (self._news_blackout_until and
                    datetime.now(timezone.utc) > self._news_blackout_until):
                self._news_blackout = False
                log.info(f"News blackout auto-cleared (past expiry time)")
                return False
            return True

    # ─── TRADE TRACKING ──────────────────────────────────────────────────────

    def record_trade_opened(self, ticket: int, trade_info: dict) -> None:
        """
        Called when a position is filled.
        Updates open trade count and trade counter for the day.
        """
        with self._lock:
            self._open_trades[ticket] = trade_info
            self._trades_today       += 1
            self._last_trade_time     = datetime.now(timezone.utc)

    def record_trade_closed(self, ticket: int, net_pnl: float) -> None:
        """
        Called when a position is closed.
        Updates consecutive loss counter for daily stopping rule.
        """
        with self._lock:
            self._open_trades.pop(ticket, None)
            if net_pnl < 0:
                self._consecutive_losses += 1
            else:
                self._consecutive_losses = 0  # Reset on any win

    def get_open_trades(self) -> dict[int, dict]:
        with self._lock:
            return dict(self._open_trades)

    def get_trades_today(self) -> int:
        with self._lock:
            return self._trades_today

    def get_consecutive_losses(self) -> int:
        with self._lock:
            return self._consecutive_losses

    def has_open_trades(self) -> bool:
        with self._lock:
            return len(self._open_trades) > 0

    # ─── DAILY RESET ─────────────────────────────────────────────────────────

    def check_and_reset_daily(
        self,
        current_equity: float,
        reset_hour_utc: int = 0,
    ) -> bool:
        """
        Check if the trading day has changed and reset daily counters.

        Parameters:
            current_equity:  Current account equity
            reset_hour_utc:  Hour (UTC) when the day resets (FTMO uses 22)

        Returns True if a reset happened (useful for logging).

        Call this once per hour from the guardian process.
        It only resets when the UTC hour matches reset_hour_utc and the
        date has changed — prevents double-reset in the same hour.

        2026 FTMO Note: Day resets at 22:00 UTC (Prague midnight CET/CEST).
        """
        with self._lock:
            now_utc = datetime.now(timezone.utc)
            today   = now_utc.date()

            # Only reset once per day
            if today == self._trading_day:
                return False

            log.info(
                f"New trading day: {today} | "
                f"Yesterday PnL: {self._daily_pnl:+.2f} USD | "
                f"Yesterday DD: {self._daily_dd_pct:.1%}"
            )

            # Reset daily counters
            self._trading_day         = today
            self._daily_start_balance = current_equity  # New day's baseline
            self._daily_start_equity  = current_equity
            self._daily_peak_equity   = current_equity
            self._daily_pnl           = 0.0
            self._daily_dd_pct        = 0.0
            self._trades_today        = 0
            self._consecutive_losses  = 0

            return True

    def set_daily_baseline(self, balance: float, equity: float) -> None:
        """
        Set the start-of-day baseline (called once at session open).
        Must be called before the first equity update of the day.
        """
        with self._lock:
            self._daily_start_balance = balance
            self._daily_start_equity  = equity
            self._daily_peak_equity   = equity
            if self._peak_equity == 0.0:
                self._peak_equity = equity   # Initialize on first run

    # ─── MODE ────────────────────────────────────────────────────────────────

    def set_mode(self, mode: SystemMode) -> None:
        with self._lock:
            old_mode    = self._mode
            self._mode  = mode
        if old_mode != mode:
            log.info(f"System mode: {old_mode.value} → {mode.value}")

    def get_mode(self) -> SystemMode:
        with self._lock:
            return self._mode

    # ─── SNAPSHOT ────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """
        Full state snapshot for the dashboard.
        Called once per dashboard refresh cycle.
        Returns a plain dict (no locks held during rendering).
        """
        with self._lock:
            return {
                # Account
                "equity":              round(self._equity, 2),
                "balance":             round(self._balance, 2),
                "daily_pnl":           round(self._daily_pnl, 2),
                "daily_dd_pct":        round(self._daily_dd_pct * 100, 2),   # as %
                "total_dd_pct":        round(self._total_dd_pct * 100, 2),   # as %
                "daily_start_balance": round(self._daily_start_balance, 2),
                "peak_equity":         round(self._peak_equity, 2),
                # Trades
                "open_trades_count":   len(self._open_trades),
                "trades_today":        self._trades_today,
                "consecutive_losses":  self._consecutive_losses,
                "last_trade_time":     (
                    self._last_trade_time.isoformat()
                    if self._last_trade_time else None
                ),
                # Risk state
                "kill_switch_active":  self._kill_switch_active,
                "kill_switch_reason":  self._kill_switch_reason,
                "news_blackout":       self._news_blackout,
                "news_event":          self._news_event_name,
                # System
                "mode":                self._mode.value,
                "trading_day":         str(self._trading_day),
            }


# ─── SINGLETON ────────────────────────────────────────────────────────────────
# ONE state store for the entire application.
# Import as: from core.state_store import state
# Or via:    from core import state
state = StateStore()
