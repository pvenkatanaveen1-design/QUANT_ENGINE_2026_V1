"""
dashboard/runtime_status.py — Redis-aware diagnostics for Streamlit.

Streamlit runs in a different process than `python run.py`, so in-process flags
like singleton `_running` are not authoritative. Prefer Redis keys populated
by run.py (`system:{name}:*`, `pulse:*`, `mt5:connection`).
"""

from __future__ import annotations

import time
from typing import Any


def redis_pulse_snapshot() -> tuple[dict[str, Any], str | None]:
    """Return telemetry dict + optional error string."""
    try:
        from core.bus import get_value
    except Exception as exc:
        return {}, f"Redis/bus unavailable ({exc})"

    try:
        return (
            {
                "pulse_status": get_value("pulse:status", silent=True),
                "mt5_connection": get_value("mt5:connection", silent=True),
                "pulse_heartbeat": get_value("pulse:heartbeat", silent=True),
                "pulse_last_tick": get_value("pulse:last_tick_epoch", silent=True),
            },
            None,
        )
    except Exception as exc:
        return {}, str(exc)


def seconds_since(ts: Any) -> float | None:
    """Age in seconds for Redis numeric epoch; None if missing/invalid."""
    if ts is None:
        return None
    try:
        return max(0.0, time.time() - float(ts))
    except (TypeError, ValueError):
        return None


# (display label, Redis registry key or None, python module, attribute for optional local note)
ENGINE_COMPONENTS: tuple[tuple[str, str | None, str, str], ...] = (
    ("Event Bus", None, "core.event_bus", "bus"),
    ("State Store", None, "core.state_store", "state"),
    ("Storage Service", None, "services.storage_service", "storage"),
    ("Tick Sanitizer", "tick_sanitizer", "systems.data.tick_sanitizer", "sanitizer"),
    ("Market Data Hub", "market_data_hub", "systems.data.market_data_hub", "hub"),
    ("Quality Monitor", "data_quality_monitor", "systems.data.data_quality_monitor", "quality_monitor"),
    ("Regime Detector", "regime_detector", "systems.intelligence.regime_detector", "regime_detector"),
    ("Session Filter", "session_filter", "systems.intelligence.session_filter", "session_filter"),
    ("Kill Switch", "kill_switch", "risk.kill_switch", "kill_switch"),
    ("Correlation Guard", "correlation_guard", "risk.correlation_guard", "correlation_guard"),
    ("Exec Profiler", "execution_profiler", "execution.profiler", "execution_profiler"),
)


def _redis_health_row(registry_key: str) -> dict[str, Any]:
    from core import system_registry as reg

    return reg.get_system_status(registry_key)


def describe_engine_component_row(
    label: str,
    registry_key: str | None,
    module: str,
    attr: str,
    *,
    stale_heartbeat_sec: float = 75.0,
) -> dict[str, Any]:
    """Merge Redis registry truth with optional local import sanity."""
    redis_part = ""
    detail = ""

    if registry_key:
        row = _redis_health_row(registry_key)
        status = str(row.get("status") or "UNKNOWN")
        err = row.get("error")
        hb_age_s = seconds_since(row.get("heartbeat"))
        lu_age = seconds_since(row.get("last_update"))

        base_status = (
            "DEGRADED (stale heartbeat)"
            if status == "RUNNING" and hb_age_s is not None and hb_age_s > stale_heartbeat_sec
            else ("UNKNOWN (engine likely not running)" if status == "UNKNOWN" else status)
        )

        parts = [f"Redis: {base_status}"]
        if hb_age_s is not None:
            parts.append(f"HB Δ {hb_age_s:.0f}s")
        elif status == "RUNNING":
            parts.append("HB pending")
        if lu_age is not None:
            parts.append(f"status Δ {lu_age:.0f}s")
        if err:
            detail = str(err)
        redis_part = " | ".join(parts)
    else:
        redis_part = "In-process only (no Redis row)"

    local = ""
    local_running = False
    try:
        mod = __import__(module, fromlist=[attr])
        obj = getattr(mod, attr, None)
        local_running = bool(getattr(obj, "_running", False)) if obj is not None else False
        local = "Dash proc subscriptions: OFF (expected)" if registry_key else ""
        if registry_key:
            local += f" | local _running={local_running}"
        else:
            local = f"import OK | _running={local_running}"
    except Exception as exc:
        local = f"import error: {exc}"

    return {
        "Component": label,
        "Engine (Redis)": redis_part,
        "Detail": detail or "—",
        "Notes": local,
    }


def build_component_overview_table() -> list[dict[str, Any]]:
    return [
        describe_engine_component_row(label, rk, mod, attr)
        for label, rk, mod, attr in ENGINE_COMPONENTS
    ]


def feed_operational_hints(feed_report: dict[str, Any], snap: dict[str, Any]) -> list[str]:
    """Human-readable checklist lines for operators."""
    lines: list[str] = []
    ps = snap.get("pulse_status")
    mt5 = snap.get("mt5_connection")
    last_tick = snap.get("pulse_last_tick")

    lines.append(
        f"Configured symbols ({len(feed_report.get('symbols') or [])}): "
        f"{', '.join(feed_report.get('symbols') or []) or '(none)'}"
    )
    lines.append(f"SYSTEM_MODE (.env): {feed_report.get('system_mode')}")
    lines.append(f"pulse:status (Redis): {ps if ps is not None else '(no key — engine not started?)'}")
    lines.append(f"mt5:connection (Redis): {mt5 if mt5 is not None else '(unknown)'}")

    tick_age = seconds_since(last_tick)
    if tick_age is None:
        lines.append("Last tick batch (Redis pulse:last_tick_epoch): never recorded")
    else:
        lines.append(f"Last tick batch age: {tick_age:.0f}s")

    if ps == "no_symbols_configured":
        lines.append("→ Fix MT5_SYMBOLS in `.env` then restart `python run.py`.")
    elif ps == "mt5_connection_failed":
        lines.append("→ Open MetaTrader 5, log in (demo/live), ensure Markets enabled.")
    elif ps in (None, "stopped") or ps == "starting":
        lines.append("→ Start engine: `python run.py` from QUANT_ENGINE_2026 (Redis must be up).")

    if not feed_report.get("ok"):
        for issue in feed_report.get("issues") or []:
            lines.append(f"Config issue: {issue}")

    return lines


def startup_control_tower(feed_report: dict[str, Any], snap: dict[str, Any]) -> dict[str, Any]:
    """
    One-glance startup truth for operators.

    Separates:
      - ingestion health (MT5/pulse)
      - engine subscriber health (registry)
      - storage visibility mode (dashboard process)
    """
    from core import system_registry as reg
    from services.storage_service import storage

    pulse_status = str(snap.get("pulse_status") or "UNKNOWN")
    mt5_status = str(snap.get("mt5_connection") or "UNKNOWN")
    tick_age = seconds_since(snap.get("pulse_last_tick"))
    tick_fresh = tick_age is not None and tick_age < 30

    subs = ("tick_sanitizer", "market_data_hub", "data_quality_monitor")
    sub_rows = [reg.get_system_status(name) for name in subs]
    sub_running = all(str(r.get("status")) == "RUNNING" for r in sub_rows)

    storage_stats = storage.get_stats()
    duck_mode = str(storage_stats.get("duckdb_mode") or "UNKNOWN")
    duck_status = str(storage_stats.get("duckdb_status") or "UNKNOWN")

    ingestion_ok = (pulse_status == "running" and mt5_status == "connected" and tick_fresh)
    engine_ok = sub_running
    overall_ok = ingestion_ok and engine_ok and feed_report.get("ok", False)

    if overall_ok:
        summary = "LIVE FEED ACTIVE"
    elif pulse_status in ("no_symbols_configured", "mt5_connection_failed"):
        summary = "FEED CONFIG / MT5 ISSUE"
    elif pulse_status in ("starting", "stopped", "UNKNOWN"):
        summary = "ENGINE STARTUP INCOMPLETE"
    else:
        summary = "DEGRADED"

    data_path = [
        ("MT5 terminal session", "OK" if mt5_status == "connected" else mt5_status),
        ("pulse.py -> RAW_MARKET_DATA", pulse_status),
        ("tick_sanitizer", str(sub_rows[0].get("status") or "UNKNOWN")),
        ("market_data_hub", str(sub_rows[1].get("status") or "UNKNOWN")),
        ("dashboard storage mode", f"{duck_mode}/{duck_status}"),
    ]

    return {
        "summary": summary,
        "overall_ok": overall_ok,
        "ingestion_ok": ingestion_ok,
        "engine_ok": engine_ok,
        "pulse_status": pulse_status,
        "mt5_status": mt5_status,
        "tick_age_s": tick_age,
        "duck_mode": duck_mode,
        "duck_status": duck_status,
        "duck_reason": storage_stats.get("duckdb_reason") or "",
        "data_path": data_path,
    }


def page_state_banner(
    *,
    page_name: str,
    runtime_source: str,
    checks: dict[str, bool],
    waiting_reason: str = "",
    fallback_reason: str = "",
) -> dict[str, str]:
    """
    Standard page state classification for operator-facing banners.

    States: RUNNING | WAITING | DEGRADED | ERROR | FALLBACK
    """
    total = len(checks)
    ok = sum(1 for v in checks.values() if bool(v))
    missing = [k for k, v in checks.items() if not bool(v)]

    if total > 0 and ok == total:
        state = "RUNNING"
        msg = f"{page_name}: all dependencies healthy ({ok}/{total})."
    elif waiting_reason:
        state = "WAITING"
        msg = f"{page_name}: waiting — {waiting_reason}"
    elif fallback_reason:
        state = "FALLBACK"
        msg = f"{page_name}: fallback mode — {fallback_reason}"
    elif ok == 0 and total > 0:
        state = "ERROR"
        msg = f"{page_name}: dependencies unavailable ({', '.join(missing)})."
    else:
        state = "DEGRADED"
        msg = f"{page_name}: partial dependency health ({ok}/{total}); missing: {', '.join(missing)}."

    return {"state": state, "message": msg, "runtime_source": runtime_source}


def regime_pipeline_health(symbol: str = "XAUUSD", timeframe: str = "H1") -> dict[str, Any]:
    """
    Dependency-chain snapshot for Regime Monitor page.
    """
    from core import system_registry as reg
    from core.bus import get_value
    from services.storage_service import storage

    pulse = reg.get_system_status("pulse")
    sanitizer = reg.get_system_status("tick_sanitizer")
    hub = reg.get_system_status("market_data_hub")
    regime = reg.get_system_status("regime_detector")
    mt5_conn = get_value("mt5:connection", silent=True)
    last_tick = get_value("pulse:last_tick_epoch", silent=True)
    waiting_reason = get_value("regime:waiting_reason", silent=True)
    last_update = get_value("regime:last_update_ts", silent=True)
    current_label = get_value("regime:current_label", silent=True)
    transition_state = get_value("regime:transition_state", silent=True)

    candle_count = 0
    last_candle = None
    duck_error = ""
    try:
        rows = storage.execute_duckdb(
            """
            SELECT COUNT(*) AS n, MAX(time) AS last_time
            FROM candles
            WHERE symbol = ? AND timeframe = ?
            """,
            (symbol, timeframe),
        )
        if rows:
            candle_count = int(rows[0][0] or 0)
            last_candle = rows[0][1]
    except Exception as exc:
        duck_error = str(exc)

    return {
        "mt5_connection": str(mt5_conn or "UNKNOWN"),
        "pulse_status": str(pulse.get("status") or "UNKNOWN"),
        "sanitizer_status": str(sanitizer.get("status") or "UNKNOWN"),
        "hub_status": str(hub.get("status") or "UNKNOWN"),
        "regime_status": str(regime.get("status") or "UNKNOWN"),
        "last_tick_age_s": seconds_since(last_tick),
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_count": candle_count,
        "last_candle": str(last_candle) if last_candle else "",
        "required_candles": 50 if timeframe == "H1" else 100,
        "duck_mode": str(storage.get_stats().get("duckdb_mode") or "UNKNOWN"),
        "duck_status": str(storage.get_stats().get("duckdb_status") or "UNKNOWN"),
        "duck_error": duck_error,
        "waiting_reason": str(waiting_reason or ""),
        "last_update_age_s": seconds_since(last_update),
        "current_label": str(current_label or "UNKNOWN"),
        "transition_state": str(transition_state or "UNKNOWN"),
    }
