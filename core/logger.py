"""
core/logger.py — Structured categorical logging for the Quanta Forex Control Center.

OVERVIEW
--------
13 separate log categories, each with its own file:
  system.log      — startup, shutdown, mode changes, health
  data.log        — ticks, candles, gaps, quality checks
  trading.log     — signals, orders, fills, closes (WHY every trade happened)
  risk.log        — DD, spread, kill switch, funded rule violations
  execution.log   — MT5 requests, broker responses, latency, retries
  ui.log          — dashboard actions, config edits, button clicks
  dependency.log  — Redis, MT5, DuckDB, package failures
  error.log       — all exceptions, crashes, stack traces
  audit.log       — funded account decisions, blocked trades, overrides
  performance.log — slow operations, latency warnings, CPU/memory
  recovery.log    — restarts, reconciliation, orphan trade handling
  backtest.log    — backtest runs, walk-forward, Monte Carlo results
  common/all.log  — EVERY entry from every category (master log)

FORMAT
------
File:    JSON Lines — one JSON object per line (grep/jq friendly)
Console: Human-readable with colour (dev) or plain (VPS)

Every JSON entry has at minimum:
  ts, lvl, cat, component, msg

Additional structured fields are passed as keyword arguments:
  log.info("Signal generated",
           symbol="XAUUSD", direction="BUY", strategy="alpha_breakout",
           entry_price=2350.0, stop_loss=2345.0, quality_score=72.5)

USAGE
-----
# Get a logger for your component (recommended)
from core.logger import get_logger, LogCategory
log = get_logger("regime_detector", LogCategory.DATA)
log.info("Regime classified", symbol="XAUUSD", regime="STRONG_TREND", adx=32.1)

# Use category-specific convenience loggers (simplest)
from core.logger import trading_log, risk_log, execution_log
trading_log.info("Order filled", symbol="XAUUSD", entry_price=2350.0, ...)

# Backward-compatible (existing code unchanged)
from core.logger import get_logger
log = get_logger("shield")   # writes to risk.log automatically by name prefix
log.info("Daily DD: 2.3%")

BACKWARD COMPATIBILITY
----------------------
Old: get_logger("name")          → returns QuantaLogger with category guessed from name
Old: get_system_logger()         → returns system category logger
Old: get_trade_logger()          → returns trading category logger
"""

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

# ─── LOG CATEGORIES ──────────────────────────────────────────────────────────

class LogCategory:
    """
    All valid log category strings.
    Pass these as the second argument to get_logger().
    """
    SYSTEM      = "system"       # startup, shutdown, mode changes
    DATA        = "data"         # ticks, candles, data quality
    TRADING     = "trading"      # signals, orders, fills, trade lifecycle
    RISK        = "risk"         # DD, spread, kill switch, prop firm rules
    EXECUTION   = "execution"    # MT5 requests, broker responses, latency
    UI          = "ui"           # dashboard actions, config edits
    DEPENDENCY  = "dependency"   # Redis, MT5, DuckDB, package failures
    ERROR       = "error"        # all exceptions and critical failures
    AUDIT       = "audit"        # funded decisions, blocked trades, overrides
    PERFORMANCE = "performance"  # slow operations, latency spikes
    RECOVERY    = "recovery"     # restarts, reconciliation, orphan trades
    BACKTEST    = "backtest"     # backtest runs, walk-forward, Monte Carlo

    # Map component name keywords → category (for backward-compat guessing)
    _KEYWORD_MAP: dict = {
        "shield":     RISK,
        "kill":       RISK,
        "risk":       RISK,
        "compliance": RISK,
        "guard":      RISK,
        "cost":       RISK,
        "news":       RISK,
        "sizer":      RISK,
        "drawdown":   RISK,
        "trade":      TRADING,
        "signal":     TRADING,
        "order":      TRADING,
        "fill":       TRADING,
        "strategy":   TRADING,
        "alpha":      TRADING,
        "scoring":    TRADING,
        "exit":       TRADING,
        "router":     EXECUTION,
        "broker":     EXECUTION,
        "profiler":   EXECUTION,
        "execution":  EXECUTION,
        "mt5":        EXECUTION,
        "regime":     DATA,
        "session":    DATA,
        "tick":       DATA,
        "candle":     DATA,
        "hub":        DATA,
        "sanitizer":  DATA,
        "quality":    DATA,
        "data":       DATA,
        "market":     DATA,
        "pulse":      DATA,
        "recovery":   RECOVERY,
        "restart":    RECOVERY,
        "reconcile":  RECOVERY,
        "backtest":   BACKTEST,
        "walk":       BACKTEST,
        "monte":      BACKTEST,
        "research":   BACKTEST,
        "dashboard":  UI,
        "ui":         UI,
        "app":        UI,
        "system":     SYSTEM,
        "startup":    SYSTEM,
        "heartbeat":  SYSTEM,
        "health":     SYSTEM,
        "redis":      DEPENDENCY,
        "duckdb":     DEPENDENCY,
        "sqlite":     DEPENDENCY,
        "storage":    DEPENDENCY,
    }

    @classmethod
    def guess(cls, component_name: str) -> str:
        """Guess the best category from a component name string."""
        lower = component_name.lower()
        for keyword, cat in cls._KEYWORD_MAP.items():
            if keyword in lower:
                return cat
        return cls.SYSTEM


# ─── LOG ROOT AND FILE PATHS ─────────────────────────────────────────────────

_LOG_ROOT = Path(os.getenv("QUANTA_LOG_DIR", "logs"))

_CATEGORY_FILES: dict[str, Path] = {
    "all":                      _LOG_ROOT / "common" / "all.log",
    LogCategory.SYSTEM:         _LOG_ROOT / "system.log",
    LogCategory.DATA:           _LOG_ROOT / "data.log",
    LogCategory.TRADING:        _LOG_ROOT / "trading.log",
    LogCategory.RISK:           _LOG_ROOT / "risk.log",
    LogCategory.EXECUTION:      _LOG_ROOT / "execution.log",
    LogCategory.UI:             _LOG_ROOT / "ui.log",
    LogCategory.DEPENDENCY:     _LOG_ROOT / "dependency.log",
    LogCategory.ERROR:          _LOG_ROOT / "error.log",
    LogCategory.AUDIT:          _LOG_ROOT / "audit.log",
    LogCategory.PERFORMANCE:    _LOG_ROOT / "performance.log",
    LogCategory.RECOVERY:       _LOG_ROOT / "recovery.log",
    LogCategory.BACKTEST:       _LOG_ROOT / "backtest.log",
}

# Max 10 MB per file, keep 5 rotated copies
_MAX_BYTES    = 10 * 1024 * 1024
_BACKUP_COUNT = 5


# ─── FORMATTERS ──────────────────────────────────────────────────────────────

# Fields that are standard LogRecord internals — skip when serialising extras
_LOGRECORD_SKIP = frozenset({
    "args", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "message", "module", "msecs",
    "msg", "name", "pathname", "process", "processName", "relativeCreated",
    "stack_info", "thread", "threadName", "taskName",
    # our own injected extras we handle explicitly
    "category", "component",
})


class _JsonFormatter(logging.Formatter):
    """
    Formats every LogRecord as a single-line JSON object.
    Used for all file handlers — machine-parseable, grep-friendly.

    Example output:
    {"ts":"2026-05-11T12:34:56.789Z","lvl":"INFO","cat":"trading","component":"alpha_breakout",
     "msg":"Signal generated","symbol":"XAUUSD","direction":"BUY","entry_price":2350.0}
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts":        datetime.fromtimestamp(record.created, tz=timezone.utc)
                                  .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "lvl":       record.levelname,
            "cat":       getattr(record, "category", "unknown"),
            "component": getattr(record, "component", record.name),
            "msg":       record.getMessage(),
        }

        # Attach any extra structured fields (symbol, trade_id, latency_ms, etc.)
        for key, val in record.__dict__.items():
            if key not in _LOGRECORD_SKIP and not key.startswith("_"):
                entry[key] = val

        # Exception / stack trace
        if record.exc_info:
            entry["stack_trace"] = self.formatException(record.exc_info)
        elif record.exc_text:
            entry["stack_trace"] = record.exc_text

        return json.dumps(entry, default=str, ensure_ascii=False)


class _ConsoleFormatter(logging.Formatter):
    """
    Human-readable console formatter with optional colour.
    Format: 2026-05-11 12:34:56 | INFO     | trading.alpha_breakout | Signal generated
    """
    _GREY    = "\x1b[38;5;246m"
    _CYAN    = "\x1b[36m"
    _GREEN   = "\x1b[32m"
    _YELLOW  = "\x1b[33m"
    _RED     = "\x1b[31m"
    _BOLD_R  = "\x1b[1;31m"
    _RESET   = "\x1b[0m"

    _LEVEL_COLORS = {
        "DEBUG":    _GREY,
        "INFO":     _GREEN,
        "WARNING":  _YELLOW,
        "ERROR":    _RED,
        "CRITICAL": _BOLD_R,
    }

    def __init__(self, use_color: bool = True):
        super().__init__()
        self._use_color = use_color and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        category  = getattr(record, "category", "")
        component = getattr(record, "component", record.name)
        label     = f"{category}.{component}" if category else component
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        msg       = record.getMessage()

        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)

        line = f"{timestamp} | {record.levelname:<8} | {label:<35} | {msg}"

        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        line = line.encode(encoding, errors="replace").decode(encoding, errors="replace")

        if self._use_color:
            colour = self._LEVEL_COLORS.get(record.levelname, "")
            return f"{colour}{line}{self._RESET}"
        return line


# ─── INTERNAL HANDLER REGISTRY ───────────────────────────────────────────────

_handler_lock   = threading.Lock()
_all_handler:   Optional[RotatingFileHandler] = None   # shared all.log handler
_cat_handlers:  dict[str, RotatingFileHandler] = {}    # per-category file handlers
_console_handler: Optional[logging.StreamHandler] = None


def _ensure_dirs() -> None:
    """Create all log directories up-front."""
    for path in _CATEGORY_FILES.values():
        path.parent.mkdir(parents=True, exist_ok=True)


def _get_all_handler() -> RotatingFileHandler:
    """Return (and lazily create) the shared all.log handler."""
    global _all_handler
    if _all_handler is None:
        _ensure_dirs()
        h = RotatingFileHandler(
            _CATEGORY_FILES["all"],
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        h.setLevel(logging.DEBUG)
        h.setFormatter(_JsonFormatter())
        _all_handler = h
    return _all_handler


def _get_cat_handler(category: str) -> RotatingFileHandler:
    """Return (and lazily create) the per-category file handler."""
    if category not in _cat_handlers:
        _ensure_dirs()
        path = _CATEGORY_FILES.get(category, _LOG_ROOT / f"{category}.log")
        h = RotatingFileHandler(
            path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        h.setLevel(logging.DEBUG)
        h.setFormatter(_JsonFormatter())
        _cat_handlers[category] = h
    return _cat_handlers[category]


def _get_console_handler() -> logging.StreamHandler:
    """Return (and lazily create) the shared console handler."""
    global _console_handler
    if _console_handler is None:
        h = logging.StreamHandler(sys.stdout)
        h.setLevel(logging.DEBUG)
        h.setFormatter(_ConsoleFormatter(use_color=True))
        _console_handler = h
    return _console_handler


# Tracks which internal logger names we've already wired up
_wired_loggers: set[str] = set()


def _build_internal_logger(category: str) -> logging.Logger:
    """
    Build (or retrieve) the Python logger for a given category.
    Each category logger writes to:
      1. logs/{category}.log  (JSON, category-specific)
      2. logs/common/all.log  (JSON, master log)
      3. stdout               (human-readable, console)

    Thread-local log_context fields are automatically injected via
    the _ContextInjectFilter attached to every logger.
    """
    logger_name = f"quanta.{category}"

    with _handler_lock:
        if logger_name not in _wired_loggers:
            # Import here to avoid circular import (log_context imports logging)
            from core.log_context import CONTEXT_FILTER

            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.DEBUG)
            logger.propagate = False  # don't bubble to root logger

            # Context filter injects thread-local fields into every record
            logger.addFilter(CONTEXT_FILTER)

            logger.addHandler(_get_cat_handler(category))
            logger.addHandler(_get_all_handler())
            logger.addHandler(_get_console_handler())
            _wired_loggers.add(logger_name)

    return logging.getLogger(logger_name)


# ─── ERROR CATEGORY: exceptions also always go to error.log ─────────────────

def _ensure_error_mirror(category: str, logger: logging.Logger) -> None:
    """
    For ERROR and CRITICAL level records from any category,
    also write to error.log. This is done by adding the error handler
    to every category logger with a level filter.
    """
    if category == LogCategory.ERROR:
        return  # already writing to error.log directly

    error_handler_name = "quanta_error_mirror"
    if not any(getattr(h, "_quanta_name", None) == error_handler_name
               for h in logger.handlers):
        _ensure_dirs()
        h = RotatingFileHandler(
            _CATEGORY_FILES[LogCategory.ERROR],
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        h.setLevel(logging.ERROR)  # only ERROR and CRITICAL pass through
        h.setFormatter(_JsonFormatter())
        h._quanta_name = error_handler_name  # type: ignore[attr-defined]
        logger.addHandler(h)


# ─── QUANTALOGGER: THE PUBLIC INTERFACE ──────────────────────────────────────

class QuantaLogger:
    """
    Structured logger for a specific component and category.

    Every log call produces JSON records in:
      logs/{category}.log   — category-specific file
      logs/common/all.log   — master log (everything)
      stdout                — human-readable console

    ERROR and CRITICAL entries ALSO go to:
      logs/error.log        — error-only log

    Basic usage:
        log = QuantaLogger("alpha_breakout", LogCategory.TRADING)
        log.info("Signal generated", symbol="XAUUSD", direction="BUY",
                 entry_price=2350.0, strategy="alpha_breakout")

    Structured convenience methods:
        log.signal(...)         — signal generation events
        log.trade_open(...)     — trade fill events
        log.trade_close(...)    — trade close events
        log.blocked(...)        — blocked signals/trades
        log.risk_check(...)     — risk check pass/fail
        log.execution(...)      — MT5 request/response
        log.perf(...)           — performance timing
        log.audit(...)          — funded account decisions
    """

    def __init__(self, component: str, category: str):
        self.component = component
        self.category  = category
        self._logger   = _build_internal_logger(category)
        _ensure_error_mirror(category, self._logger)

    # ── Core log methods ─────────────────────────────────────────────────────

    def _format_legacy_msg(self, msg: str, args: tuple[Any, ...]) -> str:
        """Support old logging calls: log.info("x=%s", value) and "x={}"."""
        if not args:
            return msg
        try:
            return msg % args
        except Exception:
            try:
                return msg.format(*args)
            except Exception:
                return f"{msg} | args={args}"

    def _emit(self, level: int, msg: str, *args: Any, **fields) -> None:
        """Internal: emit a structured log record."""
        exc_info = fields.pop("exc_info", None)
        stack_info = fields.pop("stack_info", None)
        fields.pop("exc_info", None)
        fields.pop("stack_info", None)
        msg = self._format_legacy_msg(msg, args)
        extra = {
            "category":  self.category,
            "component": self.component,
            **fields,
        }
        self._logger.log(
            level,
            msg,
            extra=extra,
            exc_info=exc_info,
            stack_info=stack_info,
            stacklevel=3,
        )

    def debug(self, msg: str, *args: Any, **fields) -> None:
        """Verbose detail — shown only in development, not on VPS."""
        self._emit(logging.DEBUG, msg, *args, **fields)

    def info(self, msg: str, *args: Any, **fields) -> None:
        """Normal operational event."""
        self._emit(logging.INFO, msg, *args, **fields)

    def warning(self, msg: str, *args: Any, **fields) -> None:
        """Unexpected but non-fatal — investigate when time allows."""
        self._emit(logging.WARNING, msg, *args, **fields)

    def error(self, msg: str, *args: Any, **fields) -> None:
        """System failure — component may be degraded but engine continues."""
        self._emit(logging.ERROR, msg, *args, **fields)

    def critical(self, msg: str, *args: Any, **fields) -> None:
        """Imminent danger — kill switch, account at risk, data corruption."""
        self._emit(logging.CRITICAL, msg, *args, **fields)

    def exception(self, msg: str, *args: Any, **fields) -> None:
        """
        Log at ERROR with current exception info automatically captured.
        Call inside except blocks:
            try:
                ...
            except Exception:
                log.exception("Order placement failed", order_id=oid)
        """
        msg = self._format_legacy_msg(msg, args)
        extra = {
            "category":  self.category,
            "component": self.component,
            **fields,
        }
        self._logger.exception(msg, extra=extra, stacklevel=2)

    # ── Trading-specific structured methods ──────────────────────────────────

    def signal(
        self,
        msg: str,
        *,
        symbol: str,
        direction: str,
        strategy: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        quality_score: float = None,
        session: str = None,
        regime: str = None,
        regime_confidence: float = None,
        spread_pips: float = None,
        correlation_id: str = None,
        timeframe: str = None,
        **extra,
    ) -> None:
        """
        Log a signal generation event with full trade context.
        Use this whenever a strategy fires a new signal (approved OR blocked).

        Example:
            log.signal("London breakout signal",
                       symbol="XAUUSD", direction="BUY", strategy="alpha_breakout",
                       entry_price=2350.00, stop_loss=2345.50, take_profit=2359.00,
                       quality_score=72.5, session="LONDON", regime="STRONG_TREND")
        """
        self._emit(
            logging.INFO, msg,
            event_type="SIGNAL_GENERATED",
            symbol=symbol, direction=direction, strategy=strategy,
            entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
            quality_score=quality_score, session=session, regime=regime,
            regime_confidence=regime_confidence, spread_pips=spread_pips,
            correlation_id=correlation_id, timeframe=timeframe,
            **extra,
        )

    def trade_open(
        self,
        msg: str,
        *,
        symbol: str,
        direction: str,
        lot_size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        order_id: str = None,
        trade_id: str = None,
        correlation_id: str = None,
        slippage_pips: float = None,
        latency_ms: float = None,
        spread_at_fill: float = None,
        strategy: str = None,
        session: str = None,
        regime: str = None,
        **extra,
    ) -> None:
        """
        Log a trade open / fill confirmation event.
        Call when MT5 confirms a fill.

        Example:
            log.trade_open("Trade filled",
                           symbol="XAUUSD", direction="BUY", lot_size=0.05,
                           entry_price=2350.20, stop_loss=2345.50, take_profit=2359.00,
                           order_id="MT5_12345", slippage_pips=0.2, latency_ms=180)
        """
        self._emit(
            logging.INFO, msg,
            event_type="TRADE_OPEN",
            symbol=symbol, direction=direction, lot_size=lot_size,
            entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
            order_id=order_id, trade_id=trade_id, correlation_id=correlation_id,
            slippage_pips=slippage_pips, latency_ms=latency_ms,
            spread_at_fill=spread_at_fill, strategy=strategy,
            session=session, regime=regime,
            **extra,
        )

    def trade_close(
        self,
        msg: str,
        *,
        symbol: str,
        trade_id: str,
        close_price: float,
        close_reason: str,
        net_pnl: float,
        lot_size: float = None,
        entry_price: float = None,
        duration_minutes: float = None,
        gross_pnl: float = None,
        slippage_pips: float = None,
        strategy: str = None,
        **extra,
    ) -> None:
        """
        Log a trade close event with PnL outcome.

        close_reason: "STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP",
                      "TIME_EXIT", "MANUAL", "KILL_SWITCH", "MARGIN_CALL"

        Example:
            log.trade_close("Trade closed at SL",
                            symbol="XAUUSD", trade_id="TRD_001",
                            close_price=2345.50, close_reason="STOP_LOSS",
                            net_pnl=-52.30, gross_pnl=-50.00)
        """
        level = logging.WARNING if net_pnl < 0 else logging.INFO
        self._emit(
            level, msg,
            event_type="TRADE_CLOSE",
            symbol=symbol, trade_id=trade_id, close_price=close_price,
            close_reason=close_reason, net_pnl=net_pnl, lot_size=lot_size,
            entry_price=entry_price, duration_minutes=duration_minutes,
            gross_pnl=gross_pnl, slippage_pips=slippage_pips, strategy=strategy,
            **extra,
        )

    def blocked(
        self,
        msg: str,
        *,
        reason: str,
        symbol: str = None,
        direction: str = None,
        strategy: str = None,
        correlation_id: str = None,
        blocked_by: str = None,
        value: float = None,
        threshold: float = None,
        **extra,
    ) -> None:
        """
        Log a blocked signal or trade with the reason it was blocked.
        This is the key audit trail for "why didn't the system trade?"

        reason examples: "KILL_SWITCH_ACTIVE", "DD_LIMIT_BREACH",
            "SPREAD_TOO_WIDE", "NEWS_BLACKOUT", "CORRELATION_LIMIT",
            "SCORE_BELOW_THRESHOLD", "WRONG_SESSION", "WRONG_REGIME"

        Example:
            log.blocked("Signal blocked — spread too wide",
                        reason="SPREAD_TOO_WIDE", symbol="XAUUSD",
                        value=3.2, threshold=2.0, blocked_by="cost_guard")
        """
        self._emit(
            logging.WARNING, msg,
            event_type="SIGNAL_BLOCKED",
            reason=reason, symbol=symbol, direction=direction,
            strategy=strategy, correlation_id=correlation_id,
            blocked_by=blocked_by, value=value, threshold=threshold,
            **extra,
        )

    def risk_check(
        self,
        msg: str,
        *,
        check_name: str,
        result: str,
        value: float = None,
        threshold: float = None,
        symbol: str = None,
        trade_id: str = None,
        **extra,
    ) -> None:
        """
        Log a risk check with pass/fail result.

        result: "PASS" or "FAIL"
        check_name: "DAILY_DD", "SPREAD", "NEWS_WINDOW", "CORRELATION",
                    "KILL_SWITCH", "FUNDED_RULE", "SCORE_GATE"

        Example:
            log.risk_check("Daily DD check",
                           check_name="DAILY_DD", result="PASS",
                           value=1.5, threshold=4.0)
        """
        level = logging.INFO if result == "PASS" else logging.WARNING
        self._emit(
            level, msg,
            event_type="RISK_CHECK",
            check_name=check_name, result=result,
            value=value, threshold=threshold,
            symbol=symbol, trade_id=trade_id,
            **extra,
        )

    def execution(
        self,
        msg: str,
        *,
        symbol: str,
        order_id: str = None,
        trade_id: str = None,
        result: str = None,
        expected_price: float = None,
        actual_price: float = None,
        slippage_pips: float = None,
        latency_ms: float = None,
        spread_pips: float = None,
        volume: float = None,
        direction: str = None,
        rejection_reason: str = None,
        attempt: int = None,
        **extra,
    ) -> None:
        """
        Log an MT5 execution event (request, response, rejection, retry).

        result: "SUBMITTED", "FILLED", "REJECTED", "RETRY", "TIMEOUT"

        Example:
            log.execution("Order filled by broker",
                          symbol="XAUUSD", order_id="12345", result="FILLED",
                          expected_price=2350.00, actual_price=2350.20,
                          slippage_pips=0.2, latency_ms=185)
        """
        level = logging.WARNING if result in ("REJECTED", "TIMEOUT") else logging.INFO
        self._emit(
            level, msg,
            event_type=f"EXECUTION_{result}" if result else "EXECUTION",
            symbol=symbol, order_id=order_id, trade_id=trade_id,
            result=result, expected_price=expected_price,
            actual_price=actual_price, slippage_pips=slippage_pips,
            latency_ms=latency_ms, spread_pips=spread_pips,
            volume=volume, direction=direction,
            rejection_reason=rejection_reason, attempt=attempt,
            **extra,
        )

    def perf(
        self,
        msg: str,
        *,
        operation: str,
        duration_ms: float,
        threshold_ms: float = None,
        component_detail: str = None,
        **extra,
    ) -> None:
        """
        Log a performance timing metric.
        Automatically warns if duration_ms > threshold_ms.

        Example:
            log.perf("ADX calculation",
                     operation="calculate_adx", duration_ms=12.3,
                     threshold_ms=100.0)
        """
        exceeded = threshold_ms is not None and duration_ms > threshold_ms
        level = logging.WARNING if exceeded else logging.INFO
        self._emit(
            level, msg,
            event_type="PERFORMANCE_METRIC",
            operation=operation, duration_ms=duration_ms,
            threshold_ms=threshold_ms, component_detail=component_detail,
            threshold_exceeded=exceeded,
            **extra,
        )

    def audit(
        self,
        msg: str,
        *,
        decision: str,
        reason: str,
        actor: str = "system",
        symbol: str = None,
        trade_id: str = None,
        rule_name: str = None,
        value: float = None,
        **extra,
    ) -> None:
        """
        Log a compliance / audit event. These are written to audit.log.
        Use for: funded rule enforcement, manual overrides, trade approvals/blocks.

        decision: "APPROVED", "BLOCKED", "OVERRIDE", "KILL_SWITCH_ACTIVATED",
                  "KILL_SWITCH_RESET", "RULE_VIOLATION", "CHALLENGE_AT_RISK"

        Example:
            log.audit("Trade approved by compliance engine",
                      decision="APPROVED", reason="All funded rules pass",
                      rule_name="FTMO_DAILY_DD", value=1.5, symbol="XAUUSD")
        """
        self._emit(
            logging.WARNING, msg,
            event_type="AUDIT",
            decision=decision, reason=reason, actor=actor,
            symbol=symbol, trade_id=trade_id,
            rule_name=rule_name, value=value,
            **extra,
        )

    def dependency(
        self,
        msg: str,
        *,
        service: str,
        status: str,
        error_message: str = None,
        retry_count: int = None,
        **extra,
    ) -> None:
        """
        Log a dependency failure or recovery event.

        service: "MT5", "REDIS", "DUCKDB", "SQLITE", "TELEGRAM", "FRED_API"
        status: "CONNECTED", "DISCONNECTED", "FAILED", "RETRYING", "RECOVERED"

        Example:
            log.dependency("MT5 disconnected",
                           service="MT5", status="DISCONNECTED",
                           error_message="Connection timeout after 30s")
        """
        level = logging.ERROR if status in ("FAILED", "DISCONNECTED") else logging.INFO
        self._emit(
            level, msg,
            event_type=f"DEPENDENCY_{status}",
            service=service, status=status,
            error_message=error_message, retry_count=retry_count,
            **extra,
        )

    def recovery_step(
        self,
        msg: str,
        *,
        step: str,
        result: str,
        detail: str = None,
        **extra,
    ) -> None:
        """
        Log a recovery step at startup.

        step: "STATE_RESTORE", "KILL_SWITCH_RESTORE", "DD_REBUILD",
              "MT5_RECONCILE", "ORPHAN_DETECT"
        result: "OK", "SKIPPED", "WARNING", "FAILED"

        Example:
            log.recovery_step("Kill switch restored from SQLite",
                              step="KILL_SWITCH_RESTORE", result="OK",
                              detail="Kill switch was active: DAILY_DD_BREACH")
        """
        level = logging.WARNING if result in ("WARNING", "FAILED") else logging.INFO
        self._emit(
            level, msg,
            event_type="RECOVERY_STEP",
            step=step, result=result, detail=detail,
            **extra,
        )


# ─── PUBLIC FACTORY FUNCTIONS ────────────────────────────────────────────────

_logger_registry: dict[tuple[str, str], QuantaLogger] = {}
_registry_lock = threading.Lock()


def get_logger(component: str = "app", category: str = None) -> QuantaLogger:
    """
    Get a QuantaLogger for a component.

    Parameters:
        component: Name of the module/class (e.g. "alpha_breakout", "shield")
        category:  LogCategory string. If None, guessed from component name.

    Returns:
        QuantaLogger instance (cached — safe to call multiple times)

    Examples:
        # With explicit category (recommended)
        log = get_logger("alpha_breakout", LogCategory.TRADING)
        log = get_logger("recovery_manager", LogCategory.RECOVERY)

        # Backward compatible — category guessed from name
        log = get_logger("shield")       # → LogCategory.RISK
        log = get_logger("regime_detector")  # → LogCategory.DATA
    """
    if category is None:
        category = LogCategory.guess(component)

    key = (component, category)
    with _registry_lock:
        if key not in _logger_registry:
            _logger_registry[key] = QuantaLogger(component, category)
    return _logger_registry[key]


# ─── CATEGORY-SPECIFIC CONVENIENCE LOGGERS ───────────────────────────────────
# Pre-built instances for each category so modules can import and use directly.
# Usage: from core.logger import trading_log, risk_log, execution_log

system_log      = get_logger("system",      LogCategory.SYSTEM)
data_log        = get_logger("data",        LogCategory.DATA)
trading_log     = get_logger("trading",     LogCategory.TRADING)
risk_log        = get_logger("risk",        LogCategory.RISK)
execution_log   = get_logger("execution",   LogCategory.EXECUTION)
ui_log          = get_logger("ui",          LogCategory.UI)
dependency_log  = get_logger("dependency",  LogCategory.DEPENDENCY)
error_log       = get_logger("error",       LogCategory.ERROR)
audit_log       = get_logger("audit",       LogCategory.AUDIT)
performance_log = get_logger("performance", LogCategory.PERFORMANCE)
recovery_log    = get_logger("recovery",    LogCategory.RECOVERY)
backtest_log    = get_logger("backtest",    LogCategory.BACKTEST)


# ─── BACKWARD-COMPATIBLE HELPERS ─────────────────────────────────────────────
# These match the old API so existing code continues to work without changes.

def get_system_logger() -> QuantaLogger:
    """Legacy: dedicated logger for system-critical events."""
    return get_logger("SYSTEM", LogCategory.SYSTEM)


def get_trade_logger() -> QuantaLogger:
    """Legacy: dedicated logger for trade events."""
    return get_logger("trades", LogCategory.TRADING)


# ─── CONSOLE LEVEL CONTROL ───────────────────────────────────────────────────

def set_console_level(level: int) -> None:
    """
    Adjust what appears on the console at runtime.
    Call set_console_level(logging.WARNING) on VPS to reduce noise.
    File logging is unaffected.
    """
    handler = _get_console_handler()
    handler.setLevel(level)


def silence_console() -> None:
    """Suppress all console output. File logs continue normally."""
    set_console_level(logging.CRITICAL + 1)


def verbose_console() -> None:
    """Show DEBUG and above on console (development mode)."""
    set_console_level(logging.DEBUG)


# ─── LOG FILE PATHS HELPER ────────────────────────────────────────────────────

def get_log_file_path(category: str) -> Path:
    """Return the Path of the log file for a given category."""
    return _CATEGORY_FILES.get(category, _LOG_ROOT / f"{category}.log")


def get_all_log_paths() -> dict[str, Path]:
    """Return a dict of all category → file path mappings."""
    return dict(_CATEGORY_FILES)


# ─── STARTUP: ENSURE DIRECTORIES EXIST ───────────────────────────────────────
_ensure_dirs()
