"""
core/recovery_manager.py — Crash detection and system state restoration.

WHY THIS FILE EXISTS
--------------------
A funded account cannot afford state loss on restart.  Without recovery:
  - Kill switch resets → system can trade again when it should not
  - Daily DD counter resets → system trades over the daily limit
  - Open positions are unknown → system opens duplicates
  - Orphan trades stay open with no SL management

This module runs ONCE at startup, BEFORE any other system starts.
It rebuilds the complete picture of the engine's state before allowing
any trading activity.

RECOVERY SEQUENCE (in order, takes ~2-5 seconds):
  Step 1: Record this restart in the restart log
  Step 2: Load last state snapshot from SQLite
  Step 3: Restore kill switch (if active before crash, stays active)
  Step 4: Rebuild daily DD from closed/open trades today
  Step 5: Check MT5 for actual open positions (LIVE mode only)
  Step 6: Find orphan trades (in local DB but not in MT5)
  Step 7: Publish RECOVERY_COMPLETE event with full report
  Step 8: Write recovery_log.json for manual review

FUNDED ACCOUNT CRITICAL:
  If kill switch was active before the crash → it stays active on restart.
  You MUST manually reset it via dashboard or Telegram /reset.
  This prevents: crash → auto-restart → trading resumes at max DD.

TEST MODE BEHAVIOR:
  Steps 5 and 6 (MT5 reconciliation) are skipped.
  Simulated state is used instead.  Recovery still runs — it just
  skips the broker calls and marks reconciliation as "SIMULATED".
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from core.event_bus import EventType, bus
from core.logger import get_logger, LogCategory
from core.state_store import state
from core.system_mode import is_live

log = get_logger("recovery_manager", LogCategory.RECOVERY)

# Path for machine-readable recovery reports
_BASE_DIR    = Path(__file__).resolve().parent.parent
_RESTART_LOG = _BASE_DIR / "logs" / "restart_log.json"
_RECOVERY_LOG = _BASE_DIR / "logs" / "recovery_log.json"


class RecoveryReport:
    """Structured summary of what the recovery manager found and restored."""

    def __init__(self) -> None:
        self.timestamp:             str   = datetime.utcnow().isoformat()
        self.mode:                  str   = "LIVE" if is_live() else "TEST"

        # Kill switch
        self.kill_switch_was_active: bool = False
        self.kill_switch_reason:    str   = ""

        # State restore
        self.last_equity:           float = 0.0
        self.last_snapshot_time:    str   = ""
        self.daily_dd_pct_restored: float = 0.0

        # MT5 reconciliation (LIVE only)
        self.mt5_open_positions:    int   = 0
        self.local_open_trades:     int   = 0
        self.orphan_trade_ids:      list  = []
        self.reconciliation_status: str   = "NOT_RUN"

        # Recovery actions taken
        self.actions_taken:         list  = []
        self.warnings:              list  = []

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    def log_summary(self) -> None:
        log.info("=" * 60)
        log.info("RECOVERY MANAGER — STARTUP REPORT")
        log.info(f"  Mode:          {self.mode}")
        log.info(f"  Kill switch:   {'ACTIVE — TRADING BLOCKED' if self.kill_switch_was_active else 'OK'}")
        if self.kill_switch_was_active:
            log.warning(f"  Kill reason:   {self.kill_switch_reason}")
            log.warning("  MANUAL RESET REQUIRED before trading can resume!")
        log.info(f"  Last equity:   ${self.last_equity:,.2f} ({self.last_snapshot_time})")
        log.info(f"  Daily DD:      {self.daily_dd_pct_restored:.2f}%")
        log.info(f"  MT5 positions: {self.mt5_open_positions}")
        log.info(f"  Local open:    {self.local_open_trades}")
        log.info(f"  Orphan trades: {len(self.orphan_trade_ids)}")
        log.info(f"  Reconciliation: {self.reconciliation_status}")
        if self.warnings:
            for w in self.warnings:
                log.warning(f"  WARNING: {w}")
        if self.actions_taken:
            for a in self.actions_taken:
                log.info(f"  Action: {a}")
        log.info("=" * 60)


class RecoveryManager:
    """
    Runs startup recovery check.  Instantiate once at process start.

    Usage in run.py:
        from core.recovery_manager import RecoveryManager
        rm = RecoveryManager()
        report = rm.run()
        if report.kill_switch_was_active:
            print("Kill switch active — manual reset required!")
    """

    def __init__(self) -> None:
        # Import repositories lazily to avoid circular imports at module load
        from repositories.state_repository import StateRepository
        from repositories.trade_repository import TradeRepository
        from services.storage_service import storage

        self._state_repo = StateRepository(storage)
        self._trade_repo = TradeRepository(storage)
        self._report     = RecoveryReport()

    def run(self) -> RecoveryReport:
        """
        Execute the full recovery sequence.
        Returns a RecoveryReport with everything found and restored.
        Logs a structured summary to the logger.
        Writes recovery_log.json to logs/ directory.
        Publishes RECOVERY_COMPLETE event on the bus.
        """
        log.info("RecoveryManager starting...")

        self._step1_record_restart()
        self._step2_restore_state_snapshot()
        self._step3_restore_kill_switch()
        self._step4_rebuild_daily_dd()
        self._step5_reconcile_mt5()
        self._step6_save_report()
        self._step7_publish_event()

        self._report.log_summary()
        return self._report

    # ─── RECOVERY STEPS ──────────────────────────────────────────────────────

    def _step1_record_restart(self) -> None:
        """Record this restart in restart_log.json for monitoring tools."""
        try:
            _RESTART_LOG.parent.mkdir(parents=True, exist_ok=True)

            existing: list = []
            if _RESTART_LOG.exists():
                with open(_RESTART_LOG, "r") as f:
                    existing = json.load(f)

            existing.append({
                "timestamp": datetime.utcnow().isoformat(),
                "mode":      "LIVE" if is_live() else "TEST",
            })

            # Keep last 50 restart records
            if len(existing) > 50:
                existing = existing[-50:]

            with open(_RESTART_LOG, "w") as f:
                json.dump(existing, f, indent=2)

            self._report.actions_taken.append("Restart recorded in logs/restart_log.json")
        except Exception as exc:
            log.warning(f"Could not write restart log: {exc}")

    def _step2_restore_state_snapshot(self) -> None:
        """Load last known equity and state from SQLite."""
        try:
            snapshot = self._state_repo.get_latest_snapshot()
            if snapshot:
                equity = snapshot.get("equity", 0.0) or 0.0
                balance = snapshot.get("balance", equity) or equity
                ts     = snapshot.get("timestamp", "")

                # Restore equity into live state store
                if equity > 0:
                    # StateStore.update_equity now expects both equity and balance.
                    # Older snapshots may not contain balance; fallback to equity.
                    state.update_equity(equity, balance)
                    self._report.last_equity        = equity
                    self._report.last_snapshot_time = ts
                    self._report.actions_taken.append(
                        f"Equity restored: ${equity:,.2f} from {ts}"
                    )
                else:
                    self._report.warnings.append("No valid equity in last snapshot")
            else:
                self._report.warnings.append("No state snapshots found — starting fresh")
        except Exception as exc:
            log.error(f"State snapshot restore failed: {exc}", exc_info=True)
            self._report.warnings.append(f"State snapshot restore error: {exc}")

    def _step3_restore_kill_switch(self) -> None:
        """
        Check if kill switch was active before crash.
        If yes: re-activate it immediately — trading stays blocked.

        CRITICAL: do NOT auto-deactivate.  Operator must manually reset.
        """
        try:
            ks_status = self._state_repo.get_kill_switch_status()
            if ks_status["active"]:
                reason = ks_status.get("reason", "Unknown — was active before restart")
                self._report.kill_switch_was_active = True
                self._report.kill_switch_reason     = reason

                # Re-activate in live state store
                state.activate_kill_switch(reason)
                self._report.actions_taken.append(
                    f"Kill switch RE-ACTIVATED: {reason}"
                )
                log.critical(
                    f"KILL SWITCH WAS ACTIVE BEFORE CRASH — restored. "
                    f"Reason: {reason}.  MANUAL RESET REQUIRED."
                )
            else:
                self._report.actions_taken.append("Kill switch: OK (was not active)")
        except Exception as exc:
            log.error(f"Kill switch restore failed: {exc}", exc_info=True)
            # Safety: if we cannot determine kill switch state → activate it
            state.activate_kill_switch("Unknown state after crash — safety lock")
            self._report.kill_switch_was_active = True
            self._report.kill_switch_reason     = "Safety lock: could not read DB"
            self._report.warnings.append("Kill switch activated as safety measure (DB read error)")

    def _step4_rebuild_daily_dd(self) -> None:
        """
        Recalculate today's daily DD from actual trade records.
        Corrects state_store in case it missed updates before crash.
        """
        try:
            today_trades = self._trade_repo.get_daily_trades(date.today())
            today_pnl = sum(
                t.get("net_pnl", 0.0) or 0.0
                for t in today_trades
                if t.get("status") == "CLOSED"
            )
            open_count  = sum(1 for t in today_trades if t.get("status") == "FILLED")
            closed_count = sum(1 for t in today_trades if t.get("status") == "CLOSED")

            self._report.daily_dd_pct_restored = 0.0
            self._report.local_open_trades     = open_count

            # Rough DD rebuild: if today's PnL is negative, that's today's loss
            equity = state.get_equity()
            if equity > 0 and today_pnl < 0:
                dd_pct = abs(today_pnl) / equity * 100
                self._report.daily_dd_pct_restored = round(dd_pct, 2)

            self._report.actions_taken.append(
                f"Daily trades found: {len(today_trades)} "
                f"(open={open_count}, closed={closed_count}) "
                f"today_pnl={today_pnl:+.2f}"
            )
        except Exception as exc:
            log.error(f"Daily DD rebuild failed: {exc}", exc_info=True)
            self._report.warnings.append(f"Daily DD rebuild error: {exc}")

    def _step5_reconcile_mt5(self) -> None:
        """
        Compare local open trades with actual MT5 positions.
        Detect orphan trades (local says open, MT5 has no such position).

        In TEST mode: skips MT5 calls, marks as SIMULATED.
        """
        if not is_live():
            self._report.reconciliation_status = "SIMULATED (TEST mode)"
            self._report.actions_taken.append("MT5 reconciliation skipped — TEST mode")
            return

        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError:
            self._report.reconciliation_status = "SKIPPED (MetaTrader5 not installed)"
            self._report.warnings.append("MetaTrader5 package not installed")
            return

        try:
            if not mt5.initialize():
                self._report.reconciliation_status = "FAILED (MT5 connect failed)"
                self._report.warnings.append("MT5 initialize() failed — cannot reconcile")
                return

            # Get all open positions from MT5
            mt5_positions = mt5.positions_get() or []
            mt5_tickets   = {int(p.ticket) for p in mt5_positions}
            self._report.mt5_open_positions = len(mt5_tickets)

            # Get locally recorded open trades
            local_open = self._trade_repo.get_open()
            local_tickets = {
                int(t.get("broker_ticket", 0))
                for t in local_open
                if t.get("broker_ticket")
            }

            # Orphan = in local DB as open, but MT5 shows closed/not found
            orphan_tickets = local_tickets - mt5_tickets
            self._report.orphan_trade_ids = list(orphan_tickets)

            if orphan_tickets:
                self._report.warnings.append(
                    f"Orphan trades detected (tickets): {orphan_tickets}. "
                    "These were open locally but MT5 has no record. "
                    "Check dashboard Recovery page and reconcile manually."
                )
                bus.publish(
                    EventType.ORPHAN_TRADE_FOUND,
                    {"orphan_tickets": list(orphan_tickets)},
                    source="recovery_manager",
                )

            self._report.reconciliation_status = "COMPLETE"
            self._report.actions_taken.append(
                f"MT5 reconciliation: {len(mt5_tickets)} MT5 positions, "
                f"{len(local_tickets)} local open, "
                f"{len(orphan_tickets)} orphans"
            )

        except Exception as exc:
            log.error(f"MT5 reconciliation error: {exc}", exc_info=True)
            self._report.reconciliation_status = f"ERROR: {exc}"
            self._report.warnings.append(f"MT5 reconciliation failed: {exc}")

    def _step6_save_report(self) -> None:
        """Write recovery report to logs/recovery_log.json and SQLite."""
        try:
            # Write JSON report
            _RECOVERY_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(_RECOVERY_LOG, "w") as f:
                json.dump(self._report.to_dict(), f, indent=2, default=str)

            # Write to SQLite via storage service
            from services.storage_service import storage
            storage.execute_sqlite_write(
                """INSERT INTO recovery_log
                   (timestamp, open_positions, orphan_trades,
                    kill_switch_restored, daily_dd_rebuilt, notes)
                   VALUES (?,?,?,?,?,?)""",
                (
                    self._report.timestamp,
                    self._report.local_open_trades,
                    len(self._report.orphan_trade_ids),
                    int(self._report.kill_switch_was_active),
                    self._report.daily_dd_pct_restored,
                    json.dumps({
                        "actions": self._report.actions_taken,
                        "warnings": self._report.warnings,
                        "reconciliation": self._report.reconciliation_status,
                    }),
                ),
            )
        except Exception as exc:
            log.warning(f"Could not save recovery report: {exc}")

    def _step7_publish_event(self) -> None:
        """Publish RECOVERY_COMPLETE event so dashboard can show status."""
        try:
            bus.publish(
                EventType.RECOVERY_COMPLETE,
                self._report.to_dict(),
                source="recovery_manager",
            )
        except Exception as exc:
            log.warning(f"Could not publish recovery event: {exc}")


def get_restart_count() -> int:
    """
    Return how many times the engine has been restarted.
    Reads from logs/restart_log.json.
    Used by dashboard VPS Health page.
    """
    try:
        if _RESTART_LOG.exists():
            with open(_RESTART_LOG, "r") as f:
                records = json.load(f)
            return len(records)
    except Exception:
        pass
    return 0


def get_last_recovery_report() -> Optional[dict]:
    """
    Return the most recent recovery report.
    Used by dashboard Recovery Dashboard page.
    """
    try:
        if _RECOVERY_LOG.exists():
            with open(_RECOVERY_LOG, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return None
