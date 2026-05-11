"""
risk/correlation_guard.py — S9: Correlation Guard.

WHY THIS FILE EXISTS
--------------------
For a solo trader trading only XAUUSD, the main correlation risk is:
  "Am I already in a XAUUSD trade?  Should I add another?"

Funded accounts with strict max-position rules need this.
Some prop firms count multiple XAUUSD trades as full exposure multiplication.

EXPOSURE TRACKING:
  For each open trade, we track:
    - Symbol exposure (XAUUSD lot size total)
    - USD directional exposure (long XAUUSD = long USD? No, long gold = risk-off)
    - Net directional bias (total BUY lots vs SELL lots)

BLOCKING RULES:
  1. Same symbol, same direction: BLOCKED if already at max trades/day
     (This is already handled by shield.py — correlation_guard adds deeper logic)
  2. Hedging detection: if you have BUY 0.05 and want SELL 0.05 = same price
     risk but no profit potential (funded firms ban hedging)
  3. Max XAU exposure: total lot size across all open XAUUSD trades
     Default: 0.20 lots max (0.10 = 1% risk per 0.10 lot move)

2026 FUNDED ACCOUNT REALITY:
  FTMO explicitly bans "account management" (hedging to zero risk).
  E8 bans full hedging (opposite positions with same lot sizes).
  Most prop firms count total XAUUSD exposure toward max drawdown.
  Having 0.10 lot open in each direction = 0.00 net but 0.20 lot risk.

USAGE:
  Subscribes to SIGNAL_GENERATED events.
  Checks current open positions from TradeRepository.
  If correlation risk detected → publishes SIGNAL_BLOCKED.
  If OK → passes signal through (does NOT approve — that's shield's job).
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

from core.event_bus import EventType, bus
from core.logger import get_logger, LogCategory
from core.models.signal import SignalEvent

log = get_logger("correlation_guard", LogCategory.RISK)

# Default limits (override in config/risk_rules.yaml)
MAX_XAUUSD_LOTS     = 0.20   # Maximum total XAUUSD exposure across all open trades
MAX_SAME_DIRECTION  = 1      # Max open trades in same direction at once
HEDGE_DETECTION     = True   # Block opposite-direction trades of equal size


class CorrelationGuard:
    """
    Blocks signals that would create dangerous correlated exposure.

    Subscribes to SIGNAL_GENERATED events.
    Reads open trades from trade repository.
    Blocks or passes through (does NOT approve — shield does final approval).

    Singleton — import via:
        from risk.correlation_guard import correlation_guard
    """

    def __init__(self) -> None:
        self._lock          = threading.Lock()
        self._block_count   = 0
        self._pass_count    = 0
        self._last_decision = ""
        self._running       = False

        # Load config (graceful fallback to defaults)
        try:
            from core import config
            rules = config.load("risk_rules")
            self._max_xau_lots = rules.get("max_xauusd_lots", MAX_XAUUSD_LOTS)
            self._max_same_dir = rules.get("max_same_direction", MAX_SAME_DIRECTION)
            self._hedge_detect = rules.get("hedge_detection", HEDGE_DETECTION)
        except Exception:
            self._max_xau_lots = MAX_XAUUSD_LOTS
            self._max_same_dir = MAX_SAME_DIRECTION
            self._hedge_detect = HEDGE_DETECTION

        log.info(
            f"CorrelationGuard initialized — "
            f"max_xau_lots={self._max_xau_lots} "
            f"max_same_dir={self._max_same_dir} "
            f"hedge_detect={self._hedge_detect}"
        )

    def start(self) -> None:
        """Subscribe to SIGNAL_GENERATED events."""
        bus.subscribe(EventType.SIGNAL_GENERATED, self._on_signal)
        self._running = True
        log.info("CorrelationGuard started")

    def stop(self) -> None:
        bus.unsubscribe(EventType.SIGNAL_GENERATED, self._on_signal)
        self._running = False

    # ─── EVENT HANDLER ────────────────────────────────────────────────────────

    def _on_signal(self, event) -> None:
        """
        Evaluate a new signal for correlation risk.
        Called asynchronously by ThreadPoolExecutor.
        """
        signal = event.payload
        if not isinstance(signal, SignalEvent):
            return
        if signal.approved:
            # Already approved by something else — skip
            return

        block_reason = self._check_correlation(signal)

        if block_reason:
            signal.approved = False
            signal.blocked_reason = f"CORRELATION: {block_reason}"

            with self._lock:
                self._block_count += 1
                self._last_decision = f"BLOCKED: {block_reason}"

            bus.publish(
                EventType.SIGNAL_BLOCKED,
                signal,
                source="correlation_guard",
                correlation_id=signal.correlation_id,
            )
            log.warning(f"Signal BLOCKED [{signal.symbol}]: {block_reason}")
        else:
            with self._lock:
                self._pass_count += 1
                self._last_decision = "PASSED"
            log.debug(f"Signal PASSED correlation check: {signal.symbol}")

    # ─── CORRELATION CHECKS ───────────────────────────────────────────────────

    def _check_correlation(self, signal: SignalEvent) -> str:
        """
        Run all correlation checks.  Returns empty string if OK, reason string if blocked.
        """
        open_trades = self._get_open_trades()
        symbol_trades = [t for t in open_trades if t.get("symbol") == signal.symbol]
        direction_str = signal.direction.value if signal.direction else ""

        # ── Check 1: Max total XAU exposure ──────────────────────────────────
        total_xau_lots = sum(
            t.get("volume", 0.0) for t in symbol_trades
        )
        if total_xau_lots + (signal.lot_size or 0.01) > self._max_xau_lots:
            return (
                f"XAU exposure limit: {total_xau_lots:.2f} open + "
                f"{signal.lot_size:.2f} new > {self._max_xau_lots:.2f} max"
            )

        # ── Check 2: Max same-direction trades ───────────────────────────────
        same_dir_trades = [
            t for t in symbol_trades
            if t.get("direction", "") == direction_str
        ]
        if len(same_dir_trades) >= self._max_same_dir:
            return (
                f"Max {self._max_same_dir} {direction_str} trade(s) allowed. "
                f"Already have {len(same_dir_trades)}."
            )

        # ── Check 3: Hedge detection ──────────────────────────────────────────
        if self._hedge_detect:
            opposite_dir = "SELL" if direction_str == "BUY" else "BUY"
            opposite_lots = sum(
                t.get("volume", 0.0)
                for t in symbol_trades
                if t.get("direction", "") == opposite_dir
            )
            signal_lots = signal.lot_size or 0.01
            if opposite_lots > 0 and abs(opposite_lots - signal_lots) < 0.01:
                return (
                    f"Hedging detected: {opposite_dir} {opposite_lots:.2f} lots open, "
                    f"adding {direction_str} {signal_lots:.2f} lots = same size hedge. "
                    f"Prop firms ban this — use /help for details."
                )

        return ""

    def _get_open_trades(self) -> list[dict]:
        """Get current open trades from trade repository."""
        try:
            from repositories.trade_repository import TradeRepository
            from services.storage_service import storage
            repo = TradeRepository(storage)
            return repo.get_open()
        except Exception as exc:
            log.warning(f"Could not fetch open trades for correlation check: {exc}")
            return []

    # ─── DIAGNOSTICS ──────────────────────────────────────────────────────────

    def get_exposure_summary(self) -> dict:
        """
        Return current exposure summary for dashboard display.
        Shows total open lot sizes per symbol and direction.
        """
        open_trades = self._get_open_trades()

        exposure: dict[str, dict] = {}
        for trade in open_trades:
            sym = trade.get("symbol", "UNKNOWN")
            d   = trade.get("direction", "UNKNOWN")
            vol = trade.get("volume", 0.0)
            if sym not in exposure:
                exposure[sym] = {"BUY": 0.0, "SELL": 0.0, "total": 0.0}
            exposure[sym][d] = round(exposure[sym].get(d, 0.0) + vol, 2)
            exposure[sym]["total"] = round(exposure[sym]["total"] + vol, 2)

        return {
            "exposure":         exposure,
            "block_count":      self._block_count,
            "pass_count":       self._pass_count,
            "last_decision":    self._last_decision,
            "max_xau_lots":     self._max_xau_lots,
            "running":          self._running,
        }


# ─── SINGLETON ────────────────────────────────────────────────────────────────
correlation_guard = CorrelationGuard()
