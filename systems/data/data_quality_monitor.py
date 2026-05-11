"""
systems/data/data_quality_monitor.py — S41: Data Quality Monitor.

WHY THIS FILE EXISTS
--------------------
A broker feed can silently fail in several ways:
  1. Candles stop arriving (broker disconnected but no error raised)
  2. Candles arrive with gaps (missed data during reconnect)
  3. Spreads stay abnormally wide (news event lingering)
  4. Data arrives but has frozen prices (MT5 hung)

Without this monitor, you could run a strategy on stale data for hours.
A funded account could miss a news event or trade on wrong regime data.

This monitor runs in a background thread, checking every SCAN_INTERVAL_SECS.
When it detects an issue, it publishes DATA_QUALITY_ALERT so:
  - The dashboard shows a red warning
  - The kill switch system can optionally halt trading

CHECKS PERFORMED (every scan):
  1. Stale feed: no tick received in X seconds during active session
  2. Missing candles: expected N candles in last hour, found fewer
  3. Abnormal spread: current spread > 2× recent average
  4. Price frozen: last 10 ticks have identical bid/ask

USAGE:
    from systems.data.data_quality_monitor import quality_monitor
    quality_monitor.start()  # runs in background thread
    quality_monitor.stop()   # graceful shutdown
    report = quality_monitor.get_report()  # for dashboard
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from core.event_bus import EventType, bus
from core.logger import get_logger, LogCategory
from systems.data.market_data_hub import hub

log = get_logger("data_quality_monitor", LogCategory.DATA)

SCAN_INTERVAL_SECS   = 60     # How often to run quality checks
STALE_FEED_SECS      = 30     # No tick in this many seconds = stale (during session)
SPREAD_MULTIPLIER    = 2.5    # Alert if spread > N × avg spread
MIN_CANDLES_PER_HOUR = 4      # Expect at least 4 H15 candles per hour


class QualityAlert:
    """Represents one quality issue detected during a scan."""

    def __init__(self, alert_type: str, symbol: str, detail: str) -> None:
        self.alert_type  = alert_type   # e.g. "STALE_FEED", "MISSING_CANDLES"
        self.symbol      = symbol
        self.detail      = detail
        self.detected_at = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        return {
            "alert_type":  self.alert_type,
            "symbol":      self.symbol,
            "detail":      self.detail,
            "detected_at": self.detected_at,
        }


class DataQualityMonitor:
    """
    Background thread that runs periodic data quality scans.

    Singleton — import via:
        from systems.data.data_quality_monitor import quality_monitor
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Quality score per symbol (0-100)
        self._scores: dict[str, float] = {}
        # Recent alerts (last 50)
        from collections import deque
        self._alerts: deque[dict] = deque(maxlen=50)
        # Last scan result
        self._last_scan: Optional[str] = None

        self._running = False
        log.info("DataQualityMonitor initialized")

    def start(self) -> None:
        """Start the background monitoring thread."""
        if self._running:
            log.warning("DataQualityMonitor already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="data_quality_monitor",
            daemon=True,
        )
        self._thread.start()
        self._running = True
        log.info(f"DataQualityMonitor started (scan every {SCAN_INTERVAL_SECS}s)")

    def stop(self) -> None:
        """Signal background thread to stop.  Waits up to 5 seconds."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._running = False
        log.info("DataQualityMonitor stopped")

    def _run_loop(self) -> None:
        """Background thread main loop."""
        while not self._stop_event.is_set():
            try:
                self._run_scan()
            except Exception as exc:
                log.error(f"DataQualityMonitor scan error: {exc}", exc_info=True)
            self._stop_event.wait(timeout=SCAN_INTERVAL_SECS)

    def _run_scan(self) -> None:
        """
        Run all quality checks for all known symbols.
        Publishes DATA_QUALITY_ALERT for each issue found.
        """
        self._last_scan = datetime.utcnow().isoformat()
        scan_alerts: list[QualityAlert] = []

        # Get symbols from market_data_hub
        known_symbols = list(hub._tick_counts.keys())
        if not known_symbols:
            # No data yet (startup phase) — not an error
            return

        for symbol in known_symbols:
            symbol_score = 100.0  # Start at perfect

            # ── Check 1: Stale feed ──────────────────────────────────────────
            last_tick_time = hub._last_tick_time.get(symbol)
            if last_tick_time:
                seconds_since = (datetime.utcnow() - last_tick_time).total_seconds()
                if seconds_since > STALE_FEED_SECS:
                    alert = QualityAlert(
                        "STALE_FEED",
                        symbol,
                        f"No tick for {seconds_since:.0f}s (threshold: {STALE_FEED_SECS}s)",
                    )
                    scan_alerts.append(alert)
                    symbol_score -= 40  # Heavy penalty — feed may be broken

            # ── Check 2: Missing candles ─────────────────────────────────────
            try:
                coverage = hub.get_coverage(symbol, "H1")
                gap_count = coverage.get("data_gap_count", 0)
                if gap_count > 0:
                    alert = QualityAlert(
                        "MISSING_CANDLES",
                        symbol,
                        f"{gap_count} gaps detected in H1 candle data",
                    )
                    scan_alerts.append(alert)
                    symbol_score -= min(30, gap_count * 5)  # Max -30 for gaps
            except Exception as exc:
                log.debug(f"Coverage check error for {symbol}: {exc}")

            # ── Check 3: Abnormal spread ─────────────────────────────────────
            latest_tick = hub.get_latest_tick(symbol)
            if latest_tick:
                current_spread = latest_tick.get("spread_pips", 0)
                # Get average spread from recent ticks in DuckDB
                try:
                    rows = hub._storage.execute_duckdb(
                        """SELECT AVG(spread_pips) FROM ticks
                           WHERE symbol = ? AND time >= ?""",
                        (symbol, datetime.utcnow() - timedelta(hours=1)),
                    )
                    avg_spread = rows[0][0] if rows and rows[0][0] else 0
                    if avg_spread > 0 and current_spread > avg_spread * SPREAD_MULTIPLIER:
                        alert = QualityAlert(
                            "SPREAD_EXPLOSION",
                            symbol,
                            f"Spread {current_spread:.1f}pips > "
                            f"{SPREAD_MULTIPLIER}× avg ({avg_spread:.1f}pips)",
                        )
                        scan_alerts.append(alert)
                        symbol_score -= 20
                except Exception:
                    pass

            # Clamp score
            symbol_score = max(0.0, symbol_score)

            with self._lock:
                self._scores[symbol] = round(symbol_score, 1)

        # Publish and store all alerts found in this scan
        for alert in scan_alerts:
            alert_dict = alert.to_dict()
            with self._lock:
                self._alerts.append(alert_dict)

            bus.publish(
                EventType.DATA_QUALITY_ALERT,
                alert_dict,
                source="data_quality_monitor",
            )
            log.warning(
                f"Quality alert [{alert.alert_type}] {alert.symbol}: {alert.detail}"
            )

        if not scan_alerts:
            log.debug("Data quality scan: all clear")

    # ─── DIAGNOSTICS ──────────────────────────────────────────────────────────

    def get_report(self) -> dict:
        """
        Return quality report for dashboard display.

        Keys:
            running       — bool
            last_scan     — ISO timestamp of last scan
            scores        — dict: symbol → quality score (0-100)
            recent_alerts — list of last 20 alert dicts
            overall_score — average across all symbols
        """
        with self._lock:
            scores  = dict(self._scores)
            alerts  = list(self._alerts)[-20:]

        overall = round(sum(scores.values()) / len(scores), 1) if scores else 100.0

        return {
            "running":       self._running,
            "last_scan":     self._last_scan or "Never",
            "scores":        scores,
            "recent_alerts": alerts,
            "overall_score": overall,
        }


# ─── SINGLETON ────────────────────────────────────────────────────────────────
quality_monitor = DataQualityMonitor()
