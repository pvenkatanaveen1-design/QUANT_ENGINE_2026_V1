"""
core/log_context.py — Thread-local log context for the Quanta engine.

WHAT THIS DOES
--------------
When multiple threads run simultaneously (signal processor, risk engine,
execution router), log entries can be hard to trace: "which thread placed
this order? which strategy triggered that risk check?"

This module provides a thread-local context store.
Set context once per request/trade flow, and it automatically attaches
to every log entry produced by that thread.

USAGE
-----

# At the start of a trading flow (e.g., when processing a signal):
from core.log_context import log_ctx

log_ctx.set(
    symbol="XAUUSD",
    strategy="alpha_breakout",
    correlation_id="SIG_20260511_001",
    session="LONDON",
    regime="STRONG_TREND",
    system_mode="LIVE",
)

# Now every log call in this thread automatically includes those fields:
log.info("Signal generated")
# → {"ts":"...", "cat":"trading", "msg":"Signal generated",
#    "symbol":"XAUUSD", "strategy":"alpha_breakout",
#    "correlation_id":"SIG_20260511_001", ...}

# Clear context at the end of the flow (important — threads are reused):
log_ctx.clear()

# Or use as a context manager (auto-clears on exit):
with log_ctx(symbol="XAUUSD", strategy="alpha_breakout"):
    process_signal(signal)

CONTEXT FIELDS
--------------
symbol          — trading symbol (XAUUSD)
timeframe       — chart timeframe (H1)
strategy        — strategy name (alpha_breakout)
session         — active session (LONDON, NY, ASIA, OVERLAP)
regime          — current regime (STRONG_TREND, RANGING, HIGH_VOL)
correlation_id  — signal/trade correlation ID for cross-log tracing
trade_id        — active trade ID
order_id        — active MT5 order ID
system_mode     — TEST or LIVE
"""

import threading
from contextlib import contextmanager
from typing import Any, Generator, Optional


# ─── THREAD-LOCAL STORAGE ────────────────────────────────────────────────────

_local = threading.local()

# All valid context field names
_CONTEXT_FIELDS = frozenset({
    "symbol",
    "timeframe",
    "strategy",
    "session",
    "regime",
    "correlation_id",
    "trade_id",
    "order_id",
    "system_mode",
})


class _LogContext:
    """
    Thread-local context manager for structured log fields.

    All fields set here are automatically injected into log records
    produced by QuantaLogger on the same thread.

    Thread-safe: each thread has its own isolated context.
    """

    def set(self, **fields: Any) -> None:
        """
        Set one or more context fields for the current thread.
        Fields are merged with any existing context (not replaced entirely).

        Example:
            log_ctx.set(symbol="XAUUSD", strategy="alpha_breakout")
        """
        ctx = self._get_ctx()
        for key, val in fields.items():
            if key in _CONTEXT_FIELDS:
                ctx[key] = val
            # Silently ignore unknown keys to avoid breaking on typos

    def get(self) -> dict:
        """
        Return a copy of the current thread's context dict.
        Used by QuantaLogger to inject fields into log records.
        """
        return dict(self._get_ctx())

    def clear(self, *fields: str) -> None:
        """
        Clear context fields.
        With no arguments: clears ALL fields.
        With arguments: clears only the named fields.

        Example:
            log_ctx.clear()                     # clear everything
            log_ctx.clear("trade_id", "order_id")  # clear specific fields
        """
        ctx = self._get_ctx()
        if not fields:
            ctx.clear()
        else:
            for key in fields:
                ctx.pop(key, None)

    def update_trade(self, trade_id: str, order_id: str = None) -> None:
        """Convenience: set trade/order IDs when a trade is opened."""
        self.set(trade_id=trade_id)
        if order_id is not None:
            self.set(order_id=order_id)

    def clear_trade(self) -> None:
        """Convenience: clear trade/order IDs when a trade is closed."""
        self.clear("trade_id", "order_id")

    @contextmanager
    def __call__(self, **fields: Any) -> Generator[None, None, None]:
        """
        Context manager: sets fields, yields, then restores previous state.

        Usage:
            with log_ctx(symbol="XAUUSD", strategy="alpha_breakout",
                         correlation_id="SIG_001"):
                process_signal(signal)
            # Context fields are restored to what they were before
        """
        previous = self.get()
        try:
            self.set(**fields)
            yield
        finally:
            # Restore exactly what was there before
            ctx = self._get_ctx()
            ctx.clear()
            ctx.update(previous)

    def _get_ctx(self) -> dict:
        """Return (lazily creating) the thread-local context dict."""
        if not hasattr(_local, "ctx"):
            _local.ctx = {}
        return _local.ctx

    def __repr__(self) -> str:
        return f"LogContext({self.get()})"


# ─── GLOBAL INSTANCE ─────────────────────────────────────────────────────────

log_ctx = _LogContext()


# ─── CONTEXT-INJECTING LOG FILTER ────────────────────────────────────────────

import logging


class _ContextInjectFilter(logging.Filter):
    """
    Python logging Filter that injects thread-local context fields
    into every LogRecord before it is emitted.

    This filter is automatically added to all QuantaLogger internal
    loggers so that log_ctx fields appear in every JSON log entry
    without the caller having to pass them manually.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = log_ctx.get()
        for key, val in ctx.items():
            # Only set if not already explicitly provided by the caller
            if not hasattr(record, key) or getattr(record, key) is None:
                setattr(record, key, val)
        return True  # always emit the record


# Singleton filter instance (shared across all loggers)
CONTEXT_FILTER = _ContextInjectFilter()
