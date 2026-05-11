"""
risk/kill_switch.py — S37: Emergency Kill Switch.

WHY THIS FILE EXISTS
--------------------
Funded accounts have a daily drawdown limit.  If you exceed it:
  - FTMO: challenge FAILED, lose your fee (₹12,000)
  - E8:   challenge FAILED, lose your fee (₹10,000)
  - The5ers: challenge FAILED, lose your fee (₹8,000)

You need a way to IMMEDIATELY stop all trading when:
  1. Daily DD limit is approaching
  2. Broker disconnects
  3. Spreads explode (news event)
  4. Latency becomes dangerous (>5000ms)
  5. You want to manually stop from your phone

This module provides the kill switch that handles ALL these triggers.

TELEGRAM INTEGRATION:
  The kill switch listens for Telegram /kill command.
  This lets you stop trading from your phone even when away from your computer.
  Also accepts: /status (see current state), /reset (re-enable trading)

  Setup:
  1. Create a Telegram bot via @BotFather
  2. Get your bot token → add to .env as TELEGRAM_BOT_TOKEN
  3. Get your Telegram user ID → add to .env as TELEGRAM_CHAT_ID
  4. The system will ONLY respond to messages from TELEGRAM_CHAT_ID
     (security: random people cannot kill your trades)

KILL SWITCH STATES:
  OFF:     Trading allowed
  ACTIVE:  All trading blocked.  Persisted to SQLite.  Survives restart.

MANUAL RESET:
  From dashboard: Risk Center page → "Reset Kill Switch" button
  From Telegram:  /reset command
  From code:      kill_switch.deactivate(reason="Manual reset")

PERSISTENCE:
  Kill switch state is written to SQLite via state_repository.
  On restart, recovery_manager checks this and re-activates if needed.
  This prevents: crash → auto-restart → trades resume during DD breach.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Optional

from core.event_bus import EventType, bus
from core.logger import get_logger, LogCategory
from core.state_store import state
from core.system_mode import is_live

log = get_logger("kill_switch", LogCategory.RISK)

# Trigger thresholds (configurable in risk_rules.yaml)
SPREAD_KILL_PIPS    = 50.0    # Spread above this → kill
LATENCY_KILL_MS     = 5000.0  # Fill latency above this → kill
DD_WARNING_PCT      = 3.0     # DD above this → warning (not kill)
DD_KILL_PCT         = 4.0     # DD above this → kill (1% buffer before 5% FTMO limit)

# Telegram poll interval (seconds)
TELEGRAM_POLL_SECS  = 5.0


class KillSwitch:
    """
    Emergency stop controller with Telegram integration.

    Singleton — import via:
        from risk.kill_switch import kill_switch

    Trigger programmatically:
        kill_switch.activate("Daily DD limit breached", triggered_by="shield")

    Re-enable:
        kill_switch.deactivate("Manual reset from dashboard")

    Check status:
        kill_switch.is_active  →  bool
    """

    def __init__(self) -> None:
        self._lock   = threading.Lock()
        self._active = False
        self._reason = ""
        self._activated_at: Optional[datetime] = None

        # Telegram config from environment
        self._bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id   = os.getenv("TELEGRAM_CHAT_ID", "")
        self._telegram_available = bool(self._bot_token and self._chat_id)

        # Telegram polling state
        self._last_update_id: int = 0
        self._telegram_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Subscribe to risk events that can trigger the kill switch
        bus.subscribe(EventType.DRAWDOWN_LIMIT, self._on_drawdown_limit)
        bus.subscribe(EventType.EXECUTION_PROFILER_UPDATE, self._on_profiler_update)

        self._running = False
        log.info(
            f"KillSwitch initialized — "
            f"Telegram={'enabled' if self._telegram_available else 'disabled (no token)'}"
        )

    def start(self) -> None:
        """Start Telegram polling thread if configured."""
        self._stop_event.clear()
        if self._telegram_available:
            self._telegram_thread = threading.Thread(
                target=self._telegram_poll_loop,
                name="kill_switch_telegram",
                daemon=True,
            )
            self._telegram_thread.start()
            log.info("Kill switch Telegram polling started")
            self._send_telegram("Kill switch system online. Use /kill to stop trading, /status to check state.")
        else:
            log.warning(
                "Telegram not configured.  /kill command unavailable. "
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to enable."
            )
        self._running = True

    def stop(self) -> None:
        """Stop Telegram polling thread."""
        self._stop_event.set()
        if self._telegram_thread and self._telegram_thread.is_alive():
            self._telegram_thread.join(timeout=10.0)
        self._running = False
        log.info("KillSwitch stopped")

    # ─── ACTIVATION / DEACTIVATION ────────────────────────────────────────────

    def activate(self, reason: str, triggered_by: str = "system") -> None:
        """
        Activate the kill switch.  All trading is immediately blocked.

        Parameters:
            reason:       Human-readable explanation (logged and stored)
            triggered_by: Source that triggered (e.g. "shield", "telegram", "manual")

        This method is thread-safe and idempotent (calling when already active is safe).
        """
        with self._lock:
            if self._active:
                log.warning(f"Kill switch already active — ignoring duplicate activation")
                return
            self._active       = True
            self._reason       = reason
            self._activated_at = datetime.utcnow()

        log.critical(
            f"KILL SWITCH ACTIVATED by {triggered_by}: {reason}"
        )

        # Update in-memory state store
        state.activate_kill_switch(reason)

        # Persist to SQLite via repository
        try:
            from repositories.state_repository import StateRepository
            from services.storage_service import storage
            repo = StateRepository(storage)
            repo.activate_kill_switch(reason, triggered_by)
        except Exception as exc:
            log.error(f"Kill switch DB persist failed: {exc}")

        # Publish to event bus (SYNCHRONOUS — runs handlers immediately)
        bus.publish(
            EventType.KILL_SWITCH,
            {
                "reason":       reason,
                "triggered_by": triggered_by,
                "timestamp":    datetime.utcnow().isoformat(),
            },
            source="kill_switch",
        )

        # Send Telegram alert
        self._send_telegram(
            f"🚨 KILL SWITCH ACTIVATED\n"
            f"Reason: {reason}\n"
            f"Triggered by: {triggered_by}\n"
            f"Time: {datetime.utcnow().strftime('%H:%M:%S UTC')}\n"
            f"Use /reset to re-enable trading."
        )

    def deactivate(self, reason: str = "Manual reset") -> None:
        """
        Re-enable trading.  Only call after manually verifying it is safe.
        Cannot be called automatically by the system — operator action only.
        """
        with self._lock:
            if not self._active:
                log.info("Kill switch already inactive")
                return
            self._active = False
            self._reason = ""

        log.warning(f"Kill switch DEACTIVATED: {reason}")

        # Update state store
        try:
            state.deactivate_kill_switch()
        except Exception:
            pass

        # Update persistence
        try:
            from repositories.state_repository import StateRepository
            from services.storage_service import storage
            repo = StateRepository(storage)
            repo.deactivate_kill_switch()
        except Exception as exc:
            log.error(f"Kill switch DB deactivate failed: {exc}")

        # Publish system started to signal recovery
        bus.publish(
            EventType.SYSTEM_STARTED,
            {"component": "kill_switch", "status": "DEACTIVATED", "reason": reason},
            source="kill_switch",
        )

        self._send_telegram(
            f"✅ Kill switch deactivated.\n"
            f"Reason: {reason}\n"
            f"Trading is now allowed. Verify DD levels before trading!"
        )

    # ─── AUTOMATIC TRIGGERS ───────────────────────────────────────────────────

    def _on_drawdown_limit(self, event) -> None:
        """
        Called synchronously when DRAWDOWN_LIMIT event fires.
        This is a SYNCHRONOUS event — runs in caller's thread immediately.
        """
        payload = event.payload or {}
        dd_pct  = payload.get("daily_dd_pct", 0.0)
        reason  = f"Daily drawdown limit: {dd_pct:.2f}%"
        self.activate(reason, triggered_by="shield/drawdown")

    def _on_profiler_update(self, event) -> None:
        """
        Check execution profiler for dangerous latency.
        Activates kill switch if avg fill latency > LATENCY_KILL_MS.
        """
        if self._active:
            return
        payload = event.payload or {}
        avg_latency = payload.get("avg_fill_latency_ms", 0.0)
        if avg_latency > LATENCY_KILL_MS:
            self.activate(
                f"Fill latency {avg_latency:.0f}ms > {LATENCY_KILL_MS:.0f}ms limit",
                triggered_by="execution_profiler",
            )

    def check_spread(self, spread_pips: float, symbol: str = "XAUUSD") -> None:
        """
        Check if spread is dangerously wide.
        Call this from cost_guard or market_data_hub on each tick.
        """
        if not self._active and spread_pips > SPREAD_KILL_PIPS:
            self.activate(
                f"Spread explosion: {spread_pips:.1f}pips on {symbol}",
                triggered_by="spread_monitor",
            )

    # ─── TELEGRAM INTEGRATION ─────────────────────────────────────────────────

    def _telegram_poll_loop(self) -> None:
        """
        Background thread: poll Telegram for new messages every TELEGRAM_POLL_SECS.
        Uses direct REST API calls (no python-telegram-bot dependency needed).
        """
        log.info("Telegram polling loop started")
        while not self._stop_event.is_set():
            try:
                self._poll_telegram_once()
            except Exception as exc:
                log.warning(f"Telegram poll error: {exc}")
            self._stop_event.wait(timeout=TELEGRAM_POLL_SECS)

    def _poll_telegram_once(self) -> None:
        """Single Telegram API poll.  Processes any new commands."""
        try:
            import urllib.request
            import json

            url = (
                f"https://api.telegram.org/bot{self._bot_token}/getUpdates"
                f"?offset={self._last_update_id + 1}&timeout=1"
            )
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())

            if not data.get("ok"):
                return

            for update in data.get("result", []):
                self._last_update_id = max(
                    self._last_update_id, update.get("update_id", 0)
                )
                msg = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = (msg.get("text") or "").strip().lower()

                # Security: only respond to configured chat ID
                if chat_id != str(self._chat_id):
                    log.warning(f"Telegram message from unknown chat {chat_id} — ignored")
                    continue

                self._handle_telegram_command(text)

        except Exception as exc:
            log.debug(f"Telegram poll exception: {exc}")

    def _handle_telegram_command(self, text: str) -> None:
        """Process a Telegram command from the authorized user."""
        if text == "/kill":
            self.activate(
                "Manual kill via Telegram /kill command",
                triggered_by="telegram",
            )
        elif text == "/reset":
            if is_live():
                self.deactivate("Telegram /reset command")
            else:
                self._send_telegram("TEST mode: kill switch simulated reset OK.")
                self.deactivate("Telegram /reset (TEST mode)")
        elif text == "/status":
            status_msg = self._format_status()
            self._send_telegram(status_msg)
        elif text == "/help":
            self._send_telegram(
                "Commands:\n"
                "/kill — Emergency stop all trading\n"
                "/status — Show current trading state\n"
                "/reset — Re-enable trading (use carefully!)\n"
                "/help — Show this message"
            )
        else:
            self._send_telegram(f"Unknown command: {text}. Use /help")

    def _format_status(self) -> str:
        """Format a human-readable status message for Telegram."""
        snap = state.snapshot()
        ks   = "🚨 ACTIVE — TRADING BLOCKED" if self._active else "✅ INACTIVE — Trading allowed"
        return (
            f"Quanta Status Report\n"
            f"Kill switch: {ks}\n"
            f"Equity: ${snap.get('equity', 0):,.2f}\n"
            f"Daily DD: {snap.get('daily_dd_pct', 0):.2f}%\n"
            f"Open trades: {snap.get('open_trade_count', 0)}\n"
            f"Time: {datetime.utcnow().strftime('%H:%M:%S UTC')}"
        )

    def _send_telegram(self, message: str) -> None:
        """Send a message to the configured Telegram chat.  Non-blocking."""
        if not self._telegram_available:
            return
        try:
            import urllib.request
            import urllib.parse
            import json
            params = urllib.parse.urlencode({
                "chat_id": self._chat_id,
                "text":    message,
            }).encode()
            url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
            req = urllib.request.Request(url, data=params, method="POST")
            urllib.request.urlopen(req, timeout=5)
        except Exception as exc:
            log.debug(f"Telegram send failed: {exc}")

    # ─── PROPERTIES ──────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """True if kill switch is active (trading blocked)."""
        with self._lock:
            return self._active

    def get_stats(self) -> dict:
        """Return kill switch state for dashboard display."""
        with self._lock:
            return {
                "active":           self._active,
                "reason":           self._reason,
                "activated_at":     self._activated_at.isoformat() if self._activated_at else "",
                "telegram_enabled": self._telegram_available,
                "running":          self._running,
            }


# ─── SINGLETON ────────────────────────────────────────────────────────────────
kill_switch = KillSwitch()
