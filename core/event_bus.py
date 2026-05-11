"""
core/event_bus.py — Thread-safe, partially-async pub/sub event bus.

ARCHITECTURE UPGRADE: Phase 2
-------------------------------
v1 (Phase 1): All handlers ran synchronously in the caller's thread.
  Problem: A slow dashboard handler could block the data feed.
  Problem: If regime handler is slow, the kill switch might be delayed.

v2 (Phase 2, this file): ThreadPoolExecutor dispatches normal handlers in
  background threads.  CRITICAL EVENTS still run synchronously FIRST.

CRITICAL EVENT GUARANTEE (KILL_SWITCH, DRAWDOWN_LIMIT):
  These bypass the thread pool entirely.  They run in the caller's thread
  before publish() returns.  A fill taking 200ms in the thread pool never
  delays the kill switch by even 1ms.  This is non-negotiable for funded
  account safety.

THREAD POOL DESIGN:
  - max_workers=4 by default.  Set higher in config for multi-strategy runs.
  - Each handler submission is fire-and-forget.
  - Handler exceptions are caught, logged, and reported as SYSTEM_ERROR events.
  - They never crash the bus or stop other handlers.

METRICS AND DIAGNOSTICS:
  - Per-handler call count, failure count, avg execution time.
  - Event history ring buffer (last HISTORY_SIZE events).
  - get_diagnostics() returns a snapshot for the dashboard's Event Bus Monitor page.

2026 USAGE NOTE:
  For a solo trader on a 4-core VPS, 4 workers is more than enough.
  Typical handler times: dashboard=5ms, state_store=1ms, repository=2ms.
  The bottleneck is always MT5 latency (20-100ms), not internal dispatch (<1ms).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from core.logger import get_logger, LogCategory

log = get_logger("event_bus", LogCategory.SYSTEM)

# Ring buffer size — last N events kept in memory for dashboard replay.
HISTORY_SIZE = 200

# These event types run SYNCHRONOUSLY in the caller's thread.
# KILL_SWITCH and DRAWDOWN_LIMIT must fire before any other code continues.
# Add event types here only if they are safety-critical blocking operations.
SYNCHRONOUS_EVENTS: frozenset[str] = frozenset({
    "KILL_SWITCH",
    "DRAWDOWN_LIMIT",
})


# ─── EVENT TYPE CONSTANTS ─────────────────────────────────────────────────────
# Use these constants everywhere.  Never use raw strings like "kill_switch".
# Typos in strings cause silent failures.  Constants fail at import time.

class EventType:
    """All valid event types in the system.  Use these — never raw strings."""

    # ── Data Flow ────────────────────────────────────────────────────────────
    RAW_MARKET_DATA      = "RAW_MARKET_DATA"     # Raw tick from MT5; sanitizer is the only subscriber
    MARKET_DATA          = "MARKET_DATA"         # Clean tick after tick_sanitizer approval
    CANDLE_CLOSED        = "CANDLE_CLOSED"        # New H1 candle closed
    TICK_REJECTED        = "TICK_REJECTED"        # Sanitizer rejected a bad tick
    DATA_GAP_DETECTED    = "DATA_GAP_DETECTED"    # Missing candles found in DuckDB
    DATA_QUALITY_ALERT   = "DATA_QUALITY_ALERT"   # Stale feed or data issue

    # ── Intelligence ─────────────────────────────────────────────────────────
    REGIME_CHANGED       = "REGIME_CHANGED"       # Regime detector output (RegimeState)
    REGIME_PROBABILITY   = "REGIME_PROBABILITY"   # Regime probability distribution snapshot
    REGIME_TRANSITION    = "REGIME_TRANSITION"    # Regime transition state update
    SESSION_CHANGED      = "SESSION_CHANGED"      # London/NY/Asia session transition
    NEWS_ALERT           = "NEWS_ALERT"           # HIGH impact news upcoming

    # ── Strategy Signals ─────────────────────────────────────────────────────
    SIGNAL_GENERATED     = "SIGNAL_GENERATED"     # Alpha strategy output (SignalEvent)
    SIGNAL_APPROVED      = "SIGNAL_APPROVED"      # Risk engine approved
    SIGNAL_BLOCKED       = "SIGNAL_BLOCKED"       # Risk engine blocked (with reason)
    SIGNAL_SCORED        = "SIGNAL_SCORED"        # Scoring engine result

    # ── Execution ────────────────────────────────────────────────────────────
    TRADE_SUBMITTED      = "TRADE_SUBMITTED"      # Order sent to MT5
    TRADE_FILLED         = "TRADE_FILLED"         # Broker confirmed fill (TradeEvent)
    TRADE_CLOSED         = "TRADE_CLOSED"         # Position closed (TradeEvent + PnL)
    TRADE_REJECTED       = "TRADE_REJECTED"       # Broker refused the order
    EXECUTION_PROFILER_UPDATE = "EXECUTION_PROFILER_UPDATE"  # Slippage stats

    # ── Risk Events — Critical ────────────────────────────────────────────────
    DRAWDOWN_WARNING     = "DRAWDOWN_WARNING"     # DD approaching limit
    DRAWDOWN_LIMIT       = "DRAWDOWN_LIMIT"       # DD limit breached — SYNCHRONOUS
    KILL_SWITCH          = "KILL_SWITCH"          # Emergency stop — SYNCHRONOUS

    # ── System Health ─────────────────────────────────────────────────────────
    HEARTBEAT            = "HEARTBEAT"            # Component alive signal
    SYSTEM_ERROR         = "SYSTEM_ERROR"         # Unhandled error in any system
    SYSTEM_STARTED       = "SYSTEM_STARTED"       # Component came online
    SYSTEM_STOPPED       = "SYSTEM_STOPPED"       # Component went offline
    HANDLER_FAILED       = "HANDLER_FAILED"       # An event handler raised an exception

    # ── Recovery ─────────────────────────────────────────────────────────────
    RECOVERY_COMPLETE    = "RECOVERY_COMPLETE"    # Recovery manager finished startup check
    ORPHAN_TRADE_FOUND   = "ORPHAN_TRADE_FOUND"   # Trade in local DB but not in MT5


# ─── DATA CLASSES ─────────────────────────────────────────────────────────────

@dataclass
class Event:
    """
    Standard envelope wrapping every event on the bus.

    All handlers receive an Event object.
    They inspect event_type first, then cast payload to the appropriate model.
    Example:
        if event.event_type == EventType.SIGNAL_GENERATED:
            signal: SignalEvent = event.payload
    """
    event_type:      str
    payload:         Any
    source:          str       = ""
    timestamp:       datetime  = field(default_factory=datetime.utcnow)
    correlation_id:  str       = ""


@dataclass
class HandlerMetrics:
    """Tracks performance of one handler function across all calls."""
    call_count:       int   = 0
    failure_count:    int   = 0
    total_time_ms:    float = 0.0
    last_failure_msg: str   = ""

    @property
    def avg_time_ms(self) -> float:
        """Average execution time in milliseconds."""
        return round(self.total_time_ms / self.call_count, 3) if self.call_count else 0.0

    @property
    def success_rate(self) -> float:
        """Percentage of calls that succeeded (0.0 to 1.0)."""
        if self.call_count == 0:
            return 1.0
        return (self.call_count - self.failure_count) / self.call_count


# ─── EVENT BUS ────────────────────────────────────────────────────────────────

class EventBus:
    """
    Thread-safe pub/sub event bus with ThreadPoolExecutor dispatch.

    USAGE — Subscribe:
        bus.subscribe(EventType.SIGNAL_GENERATED, risk_engine.on_signal)
        bus.subscribe(EventType.KILL_SWITCH, executor.on_emergency_stop)

    USAGE — Publish:
        bus.publish(EventType.REGIME_CHANGED, regime_state, source="regime_detector")
        bus.publish(EventType.KILL_SWITCH, {"reason": "DD limit breach"}, source="shield")

    KILL SWITCH GUARANTEE:
        bus.publish(EventType.KILL_SWITCH, ...)
        # ← all KILL_SWITCH handlers have finished before this line executes
        # Thread pool handlers for other events may still be running

    DIAGNOSTIC USAGE (dashboard):
        stats = bus.get_diagnostics()
        print(stats["publish_count"])  # total events published
        print(stats["metrics"])        # per-handler timing breakdown
    """

    def __init__(self, max_workers: int = 4) -> None:
        # Map of event_type → list of handler callables
        self._subscribers:  dict[str, list[Callable]] = {}

        # Single lock for subscribers dict and metrics dict
        # RLock because handlers may call bus.publish() (re-entrant)
        self._lock = threading.RLock()

        # Thread pool for non-critical async dispatch
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="evt_bus",
        )

        # Per-handler metrics: event_type → {handler_qualname → HandlerMetrics}
        self._metrics: dict[str, dict[str, HandlerMetrics]] = defaultdict(dict)

        # Event history ring buffer (last HISTORY_SIZE events)
        self._history:      deque[dict] = deque(maxlen=HISTORY_SIZE)
        self._history_lock  = threading.Lock()

        # Aggregate counters
        self._publish_count = 0
        self._failure_count = 0

        log.info(f"EventBus v2 initialized — workers={max_workers}, "
                 f"sync_events={sorted(SYNCHRONOUS_EVENTS)}")

    # ─── SUBSCRIBE / UNSUBSCRIBE ─────────────────────────────────────────────

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """
        Register a handler for an event type.

        Called once at startup per system component.
        Multiple handlers for the same event_type = all receive it.
        Calling subscribe() twice with the same handler adds it twice (not an error).
        For testing, use unsubscribe() in teardown to avoid handler leakage.

        Parameters:
            event_type: One of the EventType constants
            handler:    Callable accepting a single Event argument
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(handler)
            # Pre-allocate metrics slot to avoid races in _invoke_handler
            handler_name = handler.__qualname__
            self._metrics[event_type].setdefault(handler_name, HandlerMetrics())
        log.debug(f"Subscribed: {handler.__qualname__} → {event_type}")

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """
        Remove a specific handler.  No-op if not found.
        Used in tests and when components shut down gracefully.
        """
        with self._lock:
            handlers = self._subscribers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)
                log.debug(f"Unsubscribed: {handler.__qualname__} from {event_type}")

    # ─── PUBLISH ─────────────────────────────────────────────────────────────

    def publish(
        self,
        event_type:     str,
        payload:        Any,
        source:         str = "",
        correlation_id: str = "",
    ) -> int:
        """
        Dispatch an event to all registered handlers.

        CRITICAL EVENTS (KILL_SWITCH, DRAWDOWN_LIMIT):
            Run synchronously in this thread.  All handlers complete before
            publish() returns.  Use for safety-critical operations only.

        ALL OTHER EVENTS:
            Submitted to ThreadPoolExecutor.  publish() returns immediately.
            Handler execution is concurrent and fire-and-forget.

        Returns:
            Number of handlers dispatched (0 if no subscribers).
            0 is not an error — normal at startup before systems connect.

        Exception guarantee:
            No exception from any handler will propagate out of publish().
            Failures are logged and reported as SYSTEM_ERROR events.
        """
        event = Event(
            event_type=event_type,
            payload=payload,
            source=source,
            correlation_id=correlation_id,
        )

        # Snapshot handlers under lock (brief) then release before dispatching
        with self._lock:
            handlers = list(self._subscribers.get(event_type, []))
            self._publish_count += 1

        # Record to history buffer (non-blocking)
        self._record_history(event)

        if not handlers:
            log.debug(f"No subscribers for: {event_type}")
            return 0

        # Route to synchronous or async dispatch
        if event_type in SYNCHRONOUS_EVENTS:
            count = self._dispatch_sync(event, handlers)
        else:
            count = self._dispatch_async(event, handlers)

        log.debug(
            f"Published {event_type} from='{source}' "
            f"handlers={count} sync={event_type in SYNCHRONOUS_EVENTS}"
        )
        return count

    def _dispatch_sync(self, event: Event, handlers: list[Callable]) -> int:
        """
        Run handlers synchronously in the caller's thread.
        Used for KILL_SWITCH and DRAWDOWN_LIMIT.
        All handlers complete before this method returns.
        """
        for handler in handlers:
            self._invoke_handler(handler, event)
        return len(handlers)

    def _dispatch_async(self, event: Event, handlers: list[Callable]) -> int:
        """
        Submit handlers to thread pool (fire-and-forget).
        Returns immediately after submission, not after handler completion.
        """
        submitted = 0
        for handler in handlers:
            try:
                self._executor.submit(self._invoke_handler, handler, event)
                submitted += 1
            except RuntimeError:
                # Executor is shut down (process exiting) — this is expected
                log.warning(
                    f"EventBus executor shut down — skipping handler: "
                    f"{handler.__qualname__} for {event.event_type}"
                )
        return submitted

    def _invoke_handler(self, handler: Callable, event: Event) -> None:
        """
        Call one handler, measure time, catch all exceptions.
        Always completes — never raises.
        """
        handler_name = handler.__qualname__
        t_start = time.perf_counter()
        success = False
        try:
            handler(event)
            success = True
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t_start) * 1000
            log.error(
                f"Handler {handler_name} failed on {event.event_type}: {exc}",
                exc_info=True,
            )
            # Update failure metrics
            with self._lock:
                m = self._metrics[event.event_type].setdefault(
                    handler_name, HandlerMetrics()
                )
                m.call_count += 1
                m.failure_count += 1
                m.total_time_ms += elapsed_ms
                m.last_failure_msg = str(exc)[:200]
                self._failure_count += 1

            # Publish SYSTEM_ERROR so dashboard can show it
            # Guard against infinite recursion: SYSTEM_ERROR handlers cannot fail here
            if event.event_type != EventType.SYSTEM_ERROR:
                self._safe_publish_error(handler_name, event.event_type, str(exc))
            return

        elapsed_ms = (time.perf_counter() - t_start) * 1000

        # Update success metrics
        with self._lock:
            m = self._metrics[event.event_type].setdefault(
                handler_name, HandlerMetrics()
            )
            m.call_count += 1
            m.total_time_ms += elapsed_ms

    def _safe_publish_error(
        self, handler_name: str, failed_event: str, error_msg: str
    ) -> None:
        """Publish SYSTEM_ERROR without risk of recursion."""
        try:
            with self._lock:
                err_handlers = list(
                    self._subscribers.get(EventType.SYSTEM_ERROR, [])
                )
            if not err_handlers:
                return
            error_event = Event(
                event_type=EventType.SYSTEM_ERROR,
                payload={
                    "handler":        handler_name,
                    "failed_event":   failed_event,
                    "error":          error_msg,
                },
                source="event_bus",
            )
            for eh in err_handlers:
                # Skip the handler that just failed to avoid loops
                if eh.__qualname__ != handler_name:
                    try:
                        self._executor.submit(self._invoke_handler, eh, error_event)
                    except RuntimeError:
                        pass
        except Exception:
            pass  # Never let error reporting crash the bus

    def _record_history(self, event: Event) -> None:
        """Add event summary to ring buffer (non-blocking)."""
        with self._history_lock:
            self._history.append({
                "event_type":    event.event_type,
                "source":        event.source,
                "timestamp":     event.timestamp.isoformat(),
                "correlation_id": event.correlation_id,
            })

    # ─── DIAGNOSTICS ─────────────────────────────────────────────────────────

    def get_diagnostics(self) -> dict:
        """
        Return JSON-serializable snapshot of bus health.
        Called by dashboard/dashboard_app.py on the Event Bus Monitor page.

        Returns dict with:
            publish_count    — total events published since startup
            failure_count    — total handler failures since startup
            subscriber_count — total number of registered handlers
            subscribers      — dict of event_type → [handler names]
            metrics          — per-handler performance breakdown
            recent_events    — last 20 events in history buffer
        """
        with self._lock:
            subscribers = {
                etype: [h.__qualname__ for h in handlers]
                for etype, handlers in self._subscribers.items()
            }
            metrics_snapshot: dict[str, dict] = {}
            for etype, handler_map in self._metrics.items():
                metrics_snapshot[etype] = {
                    name: {
                        "calls":        m.call_count,
                        "failures":     m.failure_count,
                        "avg_ms":       m.avg_time_ms,
                        "success_rate": round(m.success_rate * 100, 1),
                        "last_failure": m.last_failure_msg,
                    }
                    for name, m in handler_map.items()
                }
            pub_count  = self._publish_count
            fail_count = self._failure_count

        with self._history_lock:
            recent = list(self._history)[-20:]

        return {
            "publish_count":    pub_count,
            "failure_count":    fail_count,
            "subscriber_count": sum(len(v) for v in subscribers.values()),
            "subscribers":      subscribers,
            "metrics":          metrics_snapshot,
            "recent_events":    recent,
        }

    def subscriber_count(self, event_type: str) -> int:
        """Number of handlers registered for this event type."""
        return len(self._subscribers.get(event_type, []))

    def all_subscriptions(self) -> dict[str, list[str]]:
        """Dict of event_type → [handler qualified names].  Dashboard use."""
        with self._lock:
            return {
                etype: [h.__qualname__ for h in handlers]
                for etype, handlers in self._subscribers.items()
            }

    def shutdown(self) -> None:
        """
        Graceful shutdown — waits for queued handlers to complete.
        Call this from the main process exit handler.
        After this, publish() will silently skip async handlers.
        """
        log.info("EventBus: shutting down thread pool (waiting for handlers)...")
        self._executor.shutdown(wait=True, cancel_futures=False)
        log.info("EventBus: thread pool stopped cleanly")


# ─── SINGLETON ────────────────────────────────────────────────────────────────
# ONE bus for the entire application.
# Import via:  from core.event_bus import bus
# Or via:      from core import bus
bus = EventBus()
