"""
execution/profiler.py — S33: Execution Profiler.

WHY THIS FILE EXISTS
--------------------
Slippage is the hidden cost that destroys edge over hundreds of trades.
Without tracking it:
  - You don't know if your broker is slipping you 0.5 pips or 2 pips
  - You can't tell if slippage is getting worse (broker changing)
  - You miss the signal that your strategy's edge has been eaten by execution costs

WHAT WE TRACK:
  - Expected fill price (from signal entry_price)
  - Actual fill price (from MT5 broker confirmation)
  - Slippage = actual - expected (in pips and USD)
  - Fill latency (time from order submission to fill confirmation)
  - Spread at fill time (from tick data)

SLIPPAGE BENCHMARKS (XAUUSD ECN broker, 2026):
  Excellent: < 0.1 pips average slippage
  Good:      0.1 - 0.3 pips
  Acceptable: 0.3 - 0.7 pips
  Poor:      > 0.7 pips (consider switching brokers)
  Dangerous: > 2.0 pips (kill switch may trigger)

FILL LATENCY BENCHMARKS:
  VPS near broker: 10-50ms
  Home PC local:   50-200ms
  Remote PC:       200-500ms
  Dangerously slow: > 1000ms (trades may open at wrong price)

USAGE:
  Subscribes to TRADE_FILLED events.
  Calculates slippage and latency from TradeEvent payload.
  Publishes EXECUTION_PROFILER_UPDATE with rolling stats.
  Dashboard shows slippage trend and alerts on degradation.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from typing import Optional

from core.event_bus import EventType, bus
from core.logger import get_logger, LogCategory
from core.models.trade import TradeEvent

log = get_logger("execution_profiler", LogCategory.EXECUTION)

# Alert thresholds
SLIPPAGE_ALERT_PIPS = 1.0    # Alert if avg slippage exceeds this
LATENCY_ALERT_MS    = 2000.0  # Alert if avg latency exceeds this
SAMPLE_SIZE         = 20      # Rolling window for average calculations


class ExecutionProfiler:
    """
    Tracks fill quality metrics (slippage, latency, spread).

    Subscribes to TRADE_FILLED events.
    Publishes EXECUTION_PROFILER_UPDATE every N fills.

    Singleton — import via:
        from execution.profiler import execution_profiler
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # Rolling buffers for metrics
        self._slippage_buf:     deque[float] = deque(maxlen=SAMPLE_SIZE)
        self._latency_buf:      deque[float] = deque(maxlen=SAMPLE_SIZE)
        self._spread_buf:       deque[float] = deque(maxlen=SAMPLE_SIZE)
        self._fill_count:       int = 0
        self._alert_count:      int = 0
        self._last_alert_at:    Optional[str] = None

        # Track submission times (correlation_id → submission datetime)
        # Set this BEFORE publishing TRADE_SUBMITTED so we can calculate latency
        self._submission_times: dict[str, datetime] = {}

        self._running = False

    def start(self) -> None:
        """Subscribe to trade events."""
        bus.subscribe(EventType.TRADE_SUBMITTED, self._on_trade_submitted)
        bus.subscribe(EventType.TRADE_FILLED,    self._on_trade_filled)
        self._running = True
        log.info("ExecutionProfiler started")

    def stop(self) -> None:
        bus.unsubscribe(EventType.TRADE_SUBMITTED, self._on_trade_submitted)
        bus.unsubscribe(EventType.TRADE_FILLED,    self._on_trade_filled)
        self._running = False

    # ─── EVENT HANDLERS ───────────────────────────────────────────────────────

    def _on_trade_submitted(self, event) -> None:
        """Record the submission time for latency calculation."""
        payload = event.payload
        if isinstance(payload, dict):
            corr_id = payload.get("correlation_id", "") or event.correlation_id
        elif hasattr(payload, "correlation_id"):
            corr_id = payload.correlation_id
        else:
            return
        if corr_id:
            with self._lock:
                self._submission_times[corr_id] = datetime.utcnow()

    def _on_trade_filled(self, event) -> None:
        """
        Calculate slippage and latency when a fill arrives from the broker.
        Payload should be a TradeEvent (or dict with matching fields).
        """
        try:
            payload = event.payload
            if isinstance(payload, TradeEvent):
                self._process_trade_event(payload)
            elif isinstance(payload, dict):
                self._process_dict_fill(payload, event.correlation_id)
        except Exception as exc:
            log.error(f"ExecutionProfiler fill error: {exc}", exc_info=True)

    def _process_trade_event(self, trade: TradeEvent) -> None:
        """Process fill data from a typed TradeEvent."""
        # Slippage
        slippage = trade.slippage_pips

        # Latency
        latency_ms = self._calculate_latency(trade.correlation_id)

        # Spread
        spread = trade.spread_at_entry

        self._record_fill(
            slippage_pips  = slippage,
            latency_ms     = latency_ms,
            spread_pips    = spread,
            trade_id       = trade.correlation_id,
            symbol         = trade.symbol,
        )

    def _process_dict_fill(self, fill: dict, correlation_id: str) -> None:
        """Process fill data from a raw dict (legacy broker_bridge format)."""
        expected = float(fill.get("requested_price", 0.0))
        actual   = float(fill.get("fill_price", 0.0))
        slippage = round(abs(actual - expected) / 0.10, 2) if expected else 0.0
        latency  = self._calculate_latency(correlation_id)
        spread   = float(fill.get("spread_at_entry", 0.0))
        symbol   = fill.get("symbol", "")

        self._record_fill(slippage, latency, spread, correlation_id, symbol)

    def _record_fill(
        self,
        slippage_pips: float,
        latency_ms:    float,
        spread_pips:   float,
        trade_id:      str,
        symbol:        str,
    ) -> None:
        """Record metrics and publish update."""
        with self._lock:
            self._slippage_buf.append(slippage_pips)
            self._latency_buf.append(latency_ms)
            self._spread_buf.append(spread_pips)
            self._fill_count += 1

            # Clean up submission times (prevent memory leak)
            if trade_id in self._submission_times:
                del self._submission_times[trade_id]

        # Save to DB
        try:
            from services.storage_service import storage
            storage.execute_sqlite_write(
                """INSERT INTO execution_fills
                   (trade_id, symbol, direction, expected_price, fill_price,
                    slippage_pips, fill_latency_ms, spread_at_fill, timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (trade_id, symbol, "", 0.0, 0.0,
                 slippage_pips, latency_ms, spread_pips,
                 datetime.utcnow().isoformat()),
            )
        except Exception as exc:
            log.debug(f"Profiler DB write error: {exc}")

        # Check for alerts
        avg_slippage = self._avg(self._slippage_buf)
        avg_latency  = self._avg(self._latency_buf)

        if avg_slippage > SLIPPAGE_ALERT_PIPS or avg_latency > LATENCY_ALERT_MS:
            with self._lock:
                self._alert_count += 1
                self._last_alert_at = datetime.utcnow().isoformat()
            log.warning(
                f"Execution quality alert: "
                f"slippage={avg_slippage:.2f}pips "
                f"latency={avg_latency:.0f}ms"
            )

        # Publish update every fill
        bus.publish(
            EventType.EXECUTION_PROFILER_UPDATE,
            {
                "avg_slippage_pips": avg_slippage,
                "avg_fill_latency_ms": avg_latency,
                "avg_spread_pips":   self._avg(self._spread_buf),
                "fill_count":        self._fill_count,
                "last_slippage":     slippage_pips,
                "last_latency_ms":   latency_ms,
                "symbol":            symbol,
            },
            source="execution_profiler",
        )

        log.debug(
            f"Fill recorded [{symbol}]: "
            f"slip={slippage_pips:.2f}pips "
            f"lat={latency_ms:.0f}ms "
            f"spread={spread_pips:.2f}pips"
        )

    def _calculate_latency(self, correlation_id: str) -> float:
        """
        Calculate fill latency in milliseconds.
        Latency = time between TRADE_SUBMITTED and TRADE_FILLED events.
        Returns 0.0 if we don't have a submission time (first trade).
        """
        with self._lock:
            submitted_at = self._submission_times.get(correlation_id)
        if not submitted_at:
            return 0.0
        latency_ms = (datetime.utcnow() - submitted_at).total_seconds() * 1000
        return round(latency_ms, 1)

    @staticmethod
    def _avg(buf: deque) -> float:
        """Average of a deque.  Returns 0.0 for empty buffer."""
        return round(sum(buf) / len(buf), 3) if buf else 0.0

    def record_submission_time(self, correlation_id: str) -> None:
        """
        Manually record submission time.
        Call this from execution/router.py BEFORE sending order to MT5.
        """
        with self._lock:
            self._submission_times[correlation_id] = datetime.utcnow()

    # ─── DIAGNOSTICS ──────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return execution quality stats for dashboard display."""
        with self._lock:
            return {
                "avg_slippage_pips":    self._avg(self._slippage_buf),
                "avg_latency_ms":       self._avg(self._latency_buf),
                "avg_spread_pips":      self._avg(self._spread_buf),
                "fill_count":           self._fill_count,
                "alert_count":          self._alert_count,
                "last_alert_at":        self._last_alert_at or "N/A",
                "slippage_alert_threshold": SLIPPAGE_ALERT_PIPS,
                "latency_alert_threshold":  LATENCY_ALERT_MS,
                "running":              self._running,
            }


# ─── SINGLETON ────────────────────────────────────────────────────────────────
execution_profiler = ExecutionProfiler()
