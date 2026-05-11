"""Central Redis-backed registry for subsystem + phase status (Phase 6.1).

run.py publishes here so the Streamlit dashboard can show one consolidated view.

IMPORTANT — TWO PROCESSES
------------------------
Event-bus subscribers (sanitizer, hub, regime, …) run inside `python run.py`.
Streamlit runs separately and imports singleton modules WITHOUT calling `.start()`,
so in-process `_running` flags there default to False.

Dashboard MUST prefer Redis rows (`system:{name}:status`) written by run.py for
those components instead of relying on `_running` in this interpreter.

Heartbeat semantics (`system:{name}:heartbeat` unix epoch):
    Written periodically while run.py holds subscribers/workers alive. Missing or
    very stale heartbeat together with RUNNING indicates probable stall/crash.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.bus import get_value, set_value
from core.logger import get_logger

log = get_logger()

# In-process event subscribers started before pulse/workers (see orchestrator.RuntimeController).
EVENT_SUBSCRIBER_SYSTEMS: tuple[str, ...] = (
    "tick_sanitizer",
    "market_data_hub",
    "data_quality_monitor",
    "session_filter",
    "regime_detector",
    "kill_switch",
    "correlation_guard",
    "execution_profiler",
)

# Long-lived daemon threads spawned from run.py.
WORKER_SYSTEMS: tuple[str, ...] = (
    "config_registry",
    "clock",
    "heartbeat",
    "shield",
    "atr_engine",
    "pulse",
    "broker_bridge",
    "trade_logger",
    "order_tracker",
    "trade_manager",
)

TRACKED_SYSTEMS: tuple[str, ...] = EVENT_SUBSCRIBER_SYSTEMS + WORKER_SYSTEMS

# Phases reported alongside systems (foundation → market data → risk).
TRACKED_PHASES: tuple[str, ...] = ("foundation", "market_data", "risk")

SYSTEM_STATUSES = frozenset(
    {
        "INIT",
        "STARTING",
        "RUNNING",
        "DEGRADED",
        "WARNING",
        "FAILED",
        "STOPPED",
        "ERROR",
        "RECOVERING",
    }
)

# Phases also use COMPLETE when a milestone finishes.
PHASE_STATUSES = frozenset({"STARTING", "RUNNING", "COMPLETE", "WARNING", "FAILED", "STOPPED"})


def _utc_now_ts() -> float:
    """Unix timestamp (seconds) for last_update fields."""
    return datetime.now(timezone.utc).timestamp()


def ping_redis() -> None:
    """
    Fail fast if Redis cannot accept writes.

    Uses a throwaway key so run.py can exit before spawning threads.
    """
    set_value("system:runner:preflight", _utc_now_ts())


def _normalize_system_status(raw: str) -> str:
    token = str(raw).strip().upper()
    if token not in SYSTEM_STATUSES:
        log.warning(f"system_registry | unknown system status {raw!r} — storing WARNING")
        return "WARNING"
    return token


def _normalize_phase_status(raw: str) -> str:
    token = str(raw).strip().upper()
    if token not in PHASE_STATUSES:
        log.warning(f"system_registry | unknown phase status {raw!r} — storing WARNING")
        return "WARNING"
    return token


def _write_system_keys(name: str, status: str, error: str | None) -> None:
    """Persist canonical subsystem snapshot."""
    now = _utc_now_ts()
    set_value(f"system:{name}:status", status)
    set_value(f"system:{name}:error", error)
    set_value(f"system:{name}:last_update", now)


def touch_system_heartbeat(name: str) -> None:
    """Lightweight liveness probe written independently from major status transitions."""
    try:
        set_value(f"system:{name}:heartbeat", _utc_now_ts(), silent=True)
    except Exception as exc:
        log.warning(f"system_registry | heartbeat write failed | system={name} | {exc}")


def register_system(name: str) -> None:
    """
    Begin tracking a subsystem.

    Sets status to STARTING, clears error, updates last_update — typical first step
    right before the worker thread enters its main loop.
    """
    update_system_status(name, "STARTING", error=None)


def update_system_status(name: str, status: str, error: str | None = None) -> None:
    """
    Publish subsystem status to Redis.

    Allowed values include STARTING, RUNNING, DEGRADED, WARNING, FAILED, STOPPED, ERROR, RECOVERING.
    """
    normalized = _normalize_system_status(status)
    try:
        _write_system_keys(name, normalized, error)
        log.info(f"system_registry | system={name} | status={normalized} | error={error!r}")
    except Exception as exc:
        log.error(f"system_registry | Redis write failed | system={name} | {exc}")


def mark_system_failed(name: str, error_message: str) -> None:
    """
    Mark a subsystem FAILED and store the error text on system:{name}:error.

    Safe to call from thread wrappers when run_pulse / run_clock / etc. crash.
    """
    msg = str(error_message).strip() or "unknown error"
    try:
        _write_system_keys(name, "FAILED", msg)
        log.error(f"system_registry | system={name} | FAILED | {msg}")
    except Exception as exc:
        log.error(f"system_registry | could not publish FAILURE for {name} | {exc}")


def update_phase_status(phase: str, status: str) -> None:
    """
    Publish phase:{phase}:status (Phases may use COMPLETE when finished).

    Allowed: STARTING, RUNNING, COMPLETE, WARNING, FAILED, STOPPED.
    """
    normalized = _normalize_phase_status(status)
    try:
        set_value(f"phase:{phase}:status", normalized)
        log.info(f"system_registry | phase={phase} | status={normalized}")
    except Exception as exc:
        log.error(f"system_registry | Redis write failed | phase={phase} | {exc}")


def mark_phase_complete(phase: str) -> None:
    """Mark a phase as COMPLETE (shortcut around update_phase_status)."""
    update_phase_status(phase, "COMPLETE")


def _fetch_system_row(name: str) -> dict:
    """Read one subsystem snapshot from Redis (silent reads — dashboard polling friendly)."""
    try:
        status = get_value(f"system:{name}:status", silent=True)
        error = get_value(f"system:{name}:error", silent=True)
        lu = get_value(f"system:{name}:last_update", silent=True)
        hb = get_value(f"system:{name}:heartbeat", silent=True)
        return {
            "name": name,
            "status": status if status is not None else "UNKNOWN",
            "error": error,
            "last_update": lu,
            "heartbeat": hb,
        }
    except RuntimeError as exc:
        return {
            "name": name,
            "status": "FAILED",
            "error": str(exc),
            "last_update": None,
            "heartbeat": None,
        }


def _fetch_phase_row(phase: str) -> dict:
    """Read one phase snapshot from Redis."""
    try:
        status = get_value(f"phase:{phase}:status", silent=True)
        return {
            "phase": phase,
            "status": status if status is not None else "UNKNOWN",
            "last_update": None,
        }
    except RuntimeError as exc:
        return {
            "phase": phase,
            "status": "FAILED",
            "error": str(exc),
            "last_update": None,
        }


def get_system_status(name: str | None = None) -> dict:
    """
    Read subsystem status from Redis.

    If name is provided: returns one dict (keys: name, status, error, last_update, heartbeat).
    If name is None: returns { "pulse": {...}, "clock": {...}, ... } for all TRACKED_SYSTEMS.
    """
    if name is not None:
        return _fetch_system_row(name)
    return {n: _fetch_system_row(n) for n in TRACKED_SYSTEMS}


def get_phase_status(phase: str | None = None) -> dict:
    """
    Read phase:{phase}:status from Redis.

    If phase is None: returns a dict keyed by phase name (foundation, market_data, risk).
    """
    if phase is not None:
        return _fetch_phase_row(phase)
    return {p: _fetch_phase_row(p) for p in TRACKED_PHASES}
