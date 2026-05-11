"""
repositories/signal_repository.py — CRUD for signal records.

Every SignalEvent (approved or blocked) is persisted here.
This creates a full audit trail of every trading decision the engine made.

WHY SAVE BLOCKED SIGNALS?
  Blocked signals are as valuable as approved ones for analysis.
  If 80% of your signals are being blocked by spread guard, you know:
  1. Your broker's spread is too wide, OR
  2. Your spread threshold is too tight.
  Without storing blocked signals, you cannot diagnose this.

USAGE:
    repo = SignalRepository(storage)
    repo.insert(signal_event)
    blocked = repo.get_blocked_today()
    stats = repo.get_block_reason_summary(days=7)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from core.logger import get_logger, LogCategory
from core.models.signal import SignalEvent
from services.storage_service import StorageService

log = get_logger("signal_repository", LogCategory.TRADING)


class SignalRepository:

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage

    def insert(self, signal: SignalEvent) -> None:
        """
        Persist a signal.  Call immediately after risk engine decision.
        If correlation_id already exists, replaces it (idempotent).
        """
        direction_str = signal.direction.value if signal.direction else ""
        regime_str    = signal.regime.value    if signal.regime    else ""
        session_str   = signal.session.value   if signal.session   else ""

        sql = """
        INSERT OR REPLACE INTO signals (
            correlation_id, symbol, timeframe, direction, strategy,
            entry_price, stop_loss, take_profit, sl_pips, tp_pips, rr_ratio,
            regime, session, score, confidence, atr_at_signal, adx_at_signal,
            spread_at_signal, approved, blocked_reason, lot_size, timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        params = (
            signal.correlation_id, signal.symbol, signal.timeframe,
            direction_str, signal.strategy,
            signal.entry_price, signal.stop_loss, signal.take_profit,
            signal.sl_pips, signal.tp_pips, signal.rr_ratio,
            regime_str, session_str, signal.score, signal.confidence,
            signal.atr_at_signal, signal.adx_at_signal, signal.spread_at_signal,
            int(signal.approved), signal.blocked_reason, signal.lot_size,
            signal.timestamp.isoformat(),
        )
        self._storage.execute_sqlite_write(sql, params)
        status = "APPROVED" if signal.approved else f"BLOCKED:{signal.blocked_reason}"
        log.debug(f"Signal saved: {signal.symbol} {direction_str} | {status}")

    def get_today_approved(self) -> list[dict]:
        """Return all approved signals from today.  Used by live trading dashboard."""
        rows = self._storage.execute_sqlite(
            """SELECT * FROM signals
               WHERE DATE(timestamp) = DATE('now') AND approved = 1
               ORDER BY timestamp DESC"""
        )
        return [dict(r) for r in rows]

    def get_blocked_today(self) -> list[dict]:
        """Return all blocked signals from today with reasons."""
        rows = self._storage.execute_sqlite(
            """SELECT * FROM signals
               WHERE DATE(timestamp) = DATE('now') AND approved = 0
               ORDER BY timestamp DESC"""
        )
        return [dict(r) for r in rows]

    def get_block_reason_summary(self, days: int = 7) -> list[dict]:
        """
        Count blocked signals grouped by reason for the last N days.
        Useful for tuning: if 'SpreadTooWide' dominates, reconsider threshold.

        Returns list of dicts: [{reason, count, pct_of_blocked}, ...]
        """
        rows = self._storage.execute_sqlite(
            """SELECT blocked_reason, COUNT(*) as cnt
               FROM signals
               WHERE approved = 0 AND DATE(timestamp) >= DATE('now', ?)
               GROUP BY blocked_reason
               ORDER BY cnt DESC""",
            (f"-{days} days",),
        )
        total_blocked = sum(r["cnt"] for r in rows)
        return [
            {
                "reason": r["blocked_reason"],
                "count":  r["cnt"],
                "pct":    round(r["cnt"] / total_blocked * 100, 1) if total_blocked else 0,
            }
            for r in rows
        ]

    def get_paginated(
        self,
        page: int = 1,
        page_size: int = 50,
        approved_only: bool = False,
    ) -> list[dict]:
        where = "WHERE approved = 1" if approved_only else ""
        offset = (page - 1) * page_size
        rows = self._storage.execute_sqlite(
            f"SELECT * FROM signals {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (page_size, offset),
        )
        return [dict(r) for r in rows]
