"""
repositories/state_repository.py — Persist account state and kill switch log.

WHY THIS IS SEPARATE FROM state_store.py:
  state_store.py = in-memory live state (fast, thread-safe reads)
  state_repository.py = persistence layer (slow, durability)

  state_store updates its SQLite snapshot via this repository.
  On restart, recovery_manager loads last snapshot via this repository.

KILL SWITCH PERSISTENCE:
  When kill switch is activated, a row is inserted into kill_switch_log.
  On restart, recovery_manager reads the latest active row.
  If found: system starts in BLOCKED state immediately.
  This ensures a kill switch survives a crash — critical for funded accounts.

USAGE:
    repo = StateRepository(storage)
    repo.save_snapshot(state.snapshot())
    repo.activate_kill_switch("DD limit breach", triggered_by="shield")
    repo.get_kill_switch_status() → {"active": True, "reason": "DD limit breach"}
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.logger import get_logger, LogCategory
from services.storage_service import StorageService

log = get_logger("state_repository", LogCategory.SYSTEM)


class StateRepository:

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage

    # ─── STATE SNAPSHOTS ─────────────────────────────────────────────────────

    def save_snapshot(self, snapshot: dict) -> None:
        """
        Persist a state snapshot.  Called by state_store every 60 seconds
        and by shield every time equity updates.

        Parameters:
            snapshot: dict from state_store.snapshot() with keys:
                equity, balance, daily_dd_pct, total_dd_pct,
                open_trade_count, daily_trade_count, kill_switch_active,
                news_blackout, system_mode
        """
        sql = """
        INSERT INTO state_snapshots (
            timestamp, equity, balance, daily_dd_pct, total_dd_pct,
            open_trade_count, daily_trade_count,
            kill_switch_active, news_blackout, system_mode
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """
        params = (
            datetime.utcnow().isoformat(),
            snapshot.get("equity", 0.0),
            snapshot.get("balance", 0.0),
            snapshot.get("daily_dd_pct", 0.0),
            snapshot.get("total_dd_pct", 0.0),
            snapshot.get("open_trade_count", 0),
            snapshot.get("daily_trade_count", 0),
            int(snapshot.get("kill_switch_active", False)),
            int(snapshot.get("news_blackout", False)),
            snapshot.get("system_mode", "TEST"),
        )
        self._storage.execute_sqlite_write(sql, params)

    def get_latest_snapshot(self) -> Optional[dict]:
        """
        Return the most recent state snapshot from SQLite.
        Called by recovery_manager on startup to restore last known state.
        """
        rows = self._storage.execute_sqlite(
            "SELECT * FROM state_snapshots ORDER BY id DESC LIMIT 1"
        )
        return dict(rows[0]) if rows else None

    def get_equity_history(self, limit: int = 500) -> list[dict]:
        """
        Return equity over time for dashboard equity curve.
        Returns list of {timestamp, equity} dicts.
        """
        rows = self._storage.execute_sqlite(
            "SELECT timestamp, equity FROM state_snapshots ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [{"timestamp": r["timestamp"], "equity": r["equity"]} for r in rows]

    # ─── KILL SWITCH LOG ─────────────────────────────────────────────────────

    def activate_kill_switch(self, reason: str, triggered_by: str = "") -> int:
        """
        Insert a kill switch activation record.
        Returns the row ID for later use in deactivate_kill_switch().

        Call this from risk/kill_switch.py when any kill condition triggers.
        Also called from state_store.activate_kill_switch().
        """
        sql = """
        INSERT INTO kill_switch_log (activated_at, reason, triggered_by, active)
        VALUES (?,?,?,1)
        """
        self._storage.execute_sqlite_write(
            sql, (datetime.utcnow().isoformat(), reason, triggered_by)
        )
        # Get the row ID of the row we just inserted
        rows = self._storage.execute_sqlite(
            "SELECT id FROM kill_switch_log ORDER BY id DESC LIMIT 1"
        )
        row_id = rows[0][0] if rows else -1
        log.warning(f"Kill switch ACTIVATED in DB: {reason} by={triggered_by} id={row_id}")
        return row_id

    def deactivate_kill_switch(self, row_id: int = -1) -> None:
        """
        Mark kill switch as deactivated.
        If row_id = -1, deactivates ALL active rows (used on manual reset).
        """
        now = datetime.utcnow().isoformat()
        if row_id == -1:
            self._storage.execute_sqlite_write(
                "UPDATE kill_switch_log SET active = 0, deactivated_at = ? WHERE active = 1",
                (now,),
            )
        else:
            self._storage.execute_sqlite_write(
                "UPDATE kill_switch_log SET active = 0, deactivated_at = ? WHERE id = ?",
                (now, row_id),
            )
        log.info("Kill switch DEACTIVATED in DB")

    def get_kill_switch_status(self) -> dict:
        """
        Return current kill switch state from DB.
        Call this at startup (in recovery_manager) to restore state.

        Returns:
            {"active": bool, "reason": str, "activated_at": str, "row_id": int}
        """
        rows = self._storage.execute_sqlite(
            "SELECT * FROM kill_switch_log WHERE active = 1 ORDER BY id DESC LIMIT 1"
        )
        if not rows:
            return {"active": False, "reason": "", "activated_at": "", "row_id": -1}
        r = dict(rows[0])
        return {
            "active":       True,
            "reason":       r.get("reason", ""),
            "activated_at": r.get("activated_at", ""),
            "triggered_by": r.get("triggered_by", ""),
            "row_id":       r.get("id", -1),
        }

    def get_kill_switch_history(self, limit: int = 20) -> list[dict]:
        """Return recent kill switch events for dashboard."""
        rows = self._storage.execute_sqlite(
            "SELECT * FROM kill_switch_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]
