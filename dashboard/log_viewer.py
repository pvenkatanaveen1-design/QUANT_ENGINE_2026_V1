"""
dashboard/log_viewer.py — Structured log viewer for the Quanta dashboard.

Reads JSON Lines log files and renders them as interactive, filterable
Streamlit tables.  Used by the "Logs Viewer" page in dashboard_app.py.

FEATURES
--------
- Category selector (all 13 log types)
- Level filter (DEBUG / INFO / WARNING / ERROR / CRITICAL)
- Text search across message, component, symbol fields
- Time range filter (last N minutes / hours)
- Live auto-refresh (configurable interval)
- Color-coded severity rows
- Trade-specific view: shows symbol, direction, pnl, slippage inline
- Export to CSV
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import streamlit as st

# ─── LOG FILE LOCATIONS ───────────────────────────────────────────────────────

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
_LOG_ROOT = _ENGINE_ROOT / "logs"

LOG_FILES: dict[str, Path] = {
    "ALL (Master)"  : _LOG_ROOT / "common" / "all.log",
    "system"        : _LOG_ROOT / "system.log",
    "data"          : _LOG_ROOT / "data.log",
    "trading"       : _LOG_ROOT / "trading.log",
    "risk"          : _LOG_ROOT / "risk.log",
    "execution"     : _LOG_ROOT / "execution.log",
    "ui"            : _LOG_ROOT / "ui.log",
    "dependency"    : _LOG_ROOT / "dependency.log",
    "error"         : _LOG_ROOT / "error.log",
    "audit"         : _LOG_ROOT / "audit.log",
    "performance"   : _LOG_ROOT / "performance.log",
    "recovery"      : _LOG_ROOT / "recovery.log",
    "backtest"      : _LOG_ROOT / "backtest.log",
}

# Visual colours per level (for st.markdown styling)
_LEVEL_COLORS = {
    "DEBUG":    "#888888",
    "INFO":     "#2ecc71",
    "WARNING":  "#f39c12",
    "ERROR":    "#e74c3c",
    "CRITICAL": "#c0392b",
}

_LEVEL_ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


# ─── LOG READER ───────────────────────────────────────────────────────────────

def read_log_file(path: Path, max_lines: int = 2000) -> list[dict]:
    """
    Read a JSON Lines log file and return a list of parsed log entries.
    Reads the LAST max_lines lines (tail behaviour) for performance.
    Silently skips malformed lines.
    """
    if not path.exists():
        return []

    entries: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            # Efficient tail: read last N lines
            lines = f.readlines()
            tail  = lines[-max_lines:] if len(lines) > max_lines else lines

        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
            except json.JSONDecodeError:
                # Malformed line (e.g. partial write during rotation) — skip
                pass

    except (IOError, OSError):
        pass

    return entries


def filter_entries(
    entries: list[dict],
    min_level: str = "INFO",
    search: str = "",
    minutes_back: int = 0,
    event_type: str = "",
    symbol: str = "",
    component: str = "",
) -> list[dict]:
    """
    Apply filters to a list of log entries.

    Parameters:
        min_level   — minimum log level to show ("DEBUG", "INFO", ...)
        search      — free-text search in msg, component, reason, symbol fields
        minutes_back — 0 = all time, N = last N minutes only
        event_type  — filter by event_type field (e.g. "SIGNAL_GENERATED")
        symbol      — filter by symbol field (e.g. "XAUUSD")
        component   — filter by component field
    """
    min_idx = _LEVEL_ORDER.index(min_level) if min_level in _LEVEL_ORDER else 0
    cutoff  = None
    if minutes_back > 0:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_back)

    search_lower = search.lower()

    result = []
    for e in entries:
        # Level filter
        lvl = e.get("lvl", "INFO")
        lvl_idx = _LEVEL_ORDER.index(lvl) if lvl in _LEVEL_ORDER else 0
        if lvl_idx < min_idx:
            continue

        # Time filter
        if cutoff:
            ts_str = e.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
            except (ValueError, AttributeError):
                pass

        # Symbol filter
        if symbol and e.get("symbol", "").upper() != symbol.upper():
            continue

        # Component filter
        if component and component.lower() not in e.get("component", "").lower():
            continue

        # Event type filter
        if event_type and e.get("event_type", "") != event_type:
            continue

        # Text search
        if search_lower:
            searchable = " ".join([
                str(e.get("msg", "")),
                str(e.get("component", "")),
                str(e.get("symbol", "")),
                str(e.get("strategy", "")),
                str(e.get("reason", "")),
                str(e.get("error_message", "")),
            ]).lower()
            if search_lower not in searchable:
                continue

        result.append(e)

    return result


# ─── STREAMLIT RENDER FUNCTIONS ───────────────────────────────────────────────

def render_log_stats(entries: list[dict]) -> None:
    """Show a quick summary row: counts per level."""
    counts: dict[str, int] = defaultdict(int)
    for e in entries:
        counts[e.get("lvl", "INFO")] += 1

    cols = st.columns(5)
    for i, lvl in enumerate(_LEVEL_ORDER):
        n = counts[lvl]
        color = _LEVEL_COLORS.get(lvl, "#888")
        cols[i].markdown(
            f"<div style='text-align:center'>"
            f"<span style='color:{color};font-size:1.2em;font-weight:bold'>{n}</span><br/>"
            f"<small>{lvl}</small></div>",
            unsafe_allow_html=True,
        )


def _format_value(val: Any, key: str) -> str:
    """Format a field value for display."""
    if val is None:
        return ""
    if isinstance(val, float):
        if key in ("net_pnl", "gross_pnl"):
            sign = "+" if val >= 0 else ""
            return f"{sign}{val:.2f}"
        if key in ("slippage_pips", "spread_pips", "spread_at_fill"):
            return f"{val:.2f}p"
        if key in ("latency_ms", "duration_ms"):
            return f"{val:.0f}ms"
        if key in ("quality_score",):
            return f"{val:.1f}"
        return f"{val:.4f}"
    return str(val)


def render_log_table(
    entries: list[dict],
    columns: list[str] = None,
    max_rows: int = 200,
) -> None:
    """
    Render log entries as a Streamlit dataframe.
    Columns shown depend on the selected log category.
    """
    if not entries:
        st.info("No log entries match the current filters.")
        return

    # Default columns if not specified
    if columns is None:
        columns = ["ts", "lvl", "cat", "component", "msg"]

    # Collect all available columns from entries (union of all keys)
    all_keys: set[str] = set()
    for e in entries[-max_rows:]:
        all_keys.update(e.keys())

    # Build display columns: base columns + any extra meaningful fields present
    extra_cols = [k for k in [
        "symbol", "direction", "strategy", "session", "regime",
        "entry_price", "stop_loss", "take_profit", "net_pnl",
        "slippage_pips", "latency_ms", "quality_score",
        "check_name", "result", "value", "threshold",
        "reason", "decision", "service", "status",
        "step", "operation", "duration_ms",
        "order_id", "trade_id", "correlation_id",
        "event_type", "error_message",
    ] if k in all_keys]

    display_cols = columns + [c for c in extra_cols if c not in columns]

    # Build rows
    rows = []
    for e in reversed(entries[-max_rows:]):  # newest first
        row = {}
        for col in display_cols:
            row[col] = _format_value(e.get(col), col)
        rows.append(row)

    if not rows:
        st.info("No entries to display.")
        return

    # Compact timestamp
    for row in rows:
        if "ts" in row and len(row["ts"]) > 19:
            row["ts"] = row["ts"][11:19]  # Show HH:MM:SS only

    import pandas as pd
    df = pd.DataFrame(rows, columns=display_cols)
    st.dataframe(df, use_container_width=True, height=400)


def render_log_detail(entry: dict) -> None:
    """Render a single log entry as a formatted JSON block."""
    st.json(entry)


def render_stack_trace(entries: list[dict]) -> None:
    """Show any entries with stack traces in expandable sections."""
    error_entries = [e for e in entries if e.get("stack_trace")]
    if not error_entries:
        return

    st.subheader(f"Stack Traces ({len(error_entries)} errors)")
    for e in reversed(error_entries[-10:]):
        ts = e.get("ts", "")[:19]
        component = e.get("component", "unknown")
        msg = e.get("msg", "")
        with st.expander(f"{ts} | {component} | {msg}", expanded=False):
            st.code(e.get("stack_trace", ""), language="python")
            st.json({k: v for k, v in e.items() if k != "stack_trace"})


# ─── MAIN PAGE RENDERER ───────────────────────────────────────────────────────

def render_logs_page() -> None:
    """
    Full Streamlit page for structured log viewing.
    Call this from the main dashboard_app.py.
    """
    st.title("Logs Viewer")
    st.caption("Structured JSON logs — every category, fully searchable")

    # ── Controls Row ──────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns([2, 1, 2, 1])

    with col1:
        selected_category = st.selectbox(
            "Log Category",
            options=list(LOG_FILES.keys()),
            index=0,
        )

    with col2:
        min_level = st.selectbox(
            "Min Level",
            options=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            index=1,
        )

    with col3:
        search_text = st.text_input("Search (message, component, symbol...)", "")

    with col4:
        time_range = st.selectbox(
            "Time Range",
            options=["All time", "Last 15 min", "Last 1 hour",
                     "Last 4 hours", "Last 24 hours"],
            index=0,
        )

    # Advanced filters in expander
    with st.expander("Advanced Filters", expanded=False):
        adv_col1, adv_col2, adv_col3 = st.columns(3)
        with adv_col1:
            filter_symbol = st.text_input("Symbol", "")
        with adv_col2:
            filter_component = st.text_input("Component contains", "")
        with adv_col3:
            filter_event_type = st.text_input("Event Type (exact)", "")

    # Auto-refresh
    refresh_col, export_col, _ = st.columns([1, 1, 4])
    with refresh_col:
        auto_refresh = st.checkbox("Auto-refresh (10s)", value=False)
    with export_col:
        do_export = st.button("Export CSV")

    # ── Load and Filter ───────────────────────────────────────────────────────
    log_path = LOG_FILES.get(selected_category, _LOG_ROOT / "common" / "all.log")
    raw_entries = read_log_file(log_path, max_lines=5000)

    minutes_back = {
        "All time": 0,
        "Last 15 min": 15,
        "Last 1 hour": 60,
        "Last 4 hours": 240,
        "Last 24 hours": 1440,
    }.get(time_range, 0)

    filtered = filter_entries(
        raw_entries,
        min_level=min_level,
        search=search_text,
        minutes_back=minutes_back,
        event_type=filter_event_type.strip(),
        symbol=filter_symbol.strip(),
        component=filter_component.strip(),
    )

    # ── Stats Row ─────────────────────────────────────────────────────────────
    st.markdown("---")
    st.caption(f"Showing **{len(filtered)}** of **{len(raw_entries)}** entries from `{log_path}`")
    render_log_stats(filtered)
    st.markdown("---")

    # ── Export ────────────────────────────────────────────────────────────────
    if do_export and filtered:
        import io
        import csv
        output = io.StringIO()
        all_keys = set()
        for e in filtered:
            all_keys.update(e.keys())
        keys = sorted(all_keys)
        writer = csv.DictWriter(output, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(filtered)
        st.download_button(
            label="Download CSV",
            data=output.getvalue(),
            file_name=f"quanta_logs_{selected_category}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )

    # ── Category-specific columns ─────────────────────────────────────────────
    category_columns: dict[str, list[str]] = {
        "trading": ["ts", "lvl", "component", "msg", "symbol", "direction",
                    "strategy", "entry_price", "net_pnl", "slippage_pips"],
        "risk":    ["ts", "lvl", "component", "msg", "check_name", "result",
                    "value", "threshold", "reason", "symbol"],
        "execution": ["ts", "lvl", "component", "msg", "symbol", "order_id",
                      "result", "latency_ms", "slippage_pips", "expected_price",
                      "actual_price"],
        "audit":   ["ts", "lvl", "component", "msg", "decision", "reason",
                    "actor", "rule_name", "symbol"],
        "performance": ["ts", "lvl", "component", "msg", "operation",
                        "duration_ms", "threshold_ms", "threshold_exceeded"],
        "error":   ["ts", "lvl", "component", "msg", "error_message"],
        "dependency": ["ts", "lvl", "component", "msg", "service", "status",
                       "error_message"],
        "recovery": ["ts", "lvl", "component", "msg", "step", "result", "detail"],
        "backtest": ["ts", "lvl", "component", "msg", "strategy", "symbol",
                     "timeframe", "result"],
        "data":    ["ts", "lvl", "component", "msg", "symbol", "timeframe",
                    "event_type"],
        "system":  ["ts", "lvl", "component", "msg", "event_type"],
        "ui":      ["ts", "lvl", "component", "msg", "event_type"],
    }

    # Determine which columns to use
    cat_key = selected_category.lower().split(" ")[0].replace("all", "")
    display_cols = category_columns.get(
        cat_key,
        ["ts", "lvl", "cat", "component", "msg"]
    )

    # ── Main Table ────────────────────────────────────────────────────────────
    render_log_table(filtered, columns=display_cols, max_rows=300)

    # ── Stack Traces ─────────────────────────────────────────────────────────
    render_stack_trace(filtered)

    # ── Raw JSON Inspector ───────────────────────────────────────────────────
    with st.expander("Raw JSON Inspector (last 5 entries)", expanded=False):
        for e in reversed(filtered[-5:]):
            st.json(e)
            st.markdown("---")

    # ── File Info ─────────────────────────────────────────────────────────────
    with st.expander("Log File Locations", expanded=False):
        for name, path in LOG_FILES.items():
            exists = "✓" if path.exists() else "✗"
            size = ""
            if path.exists():
                kb = path.stat().st_size / 1024
                size = f"({kb:.1f} KB)"
            st.text(f"{exists} {name:<20} → {path}  {size}")

    # ── Auto-refresh ──────────────────────────────────────────────────────────
    if auto_refresh:
        time.sleep(10)
        st.rerun()
