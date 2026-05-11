"""
dashboard/dashboard_app.py — Quanta Forex Control Center.

Navigation is grouped in the sidebar (Operations, Risk, Research, Platform/VPS, …).
See `dashboard/nav_sections.py` for section membership + per-page setup hints.

USAGE:
  streamlit run dashboard/dashboard_app.py
"""

from __future__ import annotations

import sys
import os
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ─── SYS.PATH BOOTSTRAP ───────────────────────────────────────────────────────
# Ensure engine root is on the path regardless of where streamlit is run from.
_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

import streamlit as st

from dashboard.nav_sections import PAGE_GROUPS, PAGE_SETUP_HINT

st.set_page_config(
    page_title  = "Quanta Forex Control Center",
    page_icon   = "📊",
    layout      = "wide",
    initial_sidebar_state = "expanded",
)

st.markdown(
    """
<style>
    .block-container { padding-top: 1rem !important; padding-bottom: 2rem !important; max-width: 1400px; }
    div[data-testid="stSidebarContent"] { padding-top: 0.35rem; }
    h1 { font-size: 1.75rem !important; }
    h2 { font-size: 1.35rem !important; }
</style>
    """,
    unsafe_allow_html=True,
)

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _safe_import(module_path: str, fallback=None):
    """Safely import a module — returns fallback if import fails."""
    try:
        parts = module_path.rsplit(".", 1)
        if len(parts) == 2:
            mod = __import__(module_path.rsplit(".", 1)[0], fromlist=[parts[-1]])
            return getattr(mod, parts[-1])
        return __import__(module_path)
    except Exception:
        return fallback


def _metric_card(label: str, value: str, delta: str = "", color: str = "normal") -> None:
    """Render a single metric card."""
    st.metric(label=label, value=value, delta=delta if delta else None)


def _status_badge(text: str, ok: bool) -> str:
    """Return colored HTML badge string."""
    color = "🟢" if ok else "🔴"
    return f"{color} {text}"


# ─── SIDEBAR NAVIGATION (grouped: Ops · Risk · Research · VPS …) ───────────────

st.sidebar.title("📊 Quanta Control Center")
st.sidebar.caption("Grouped navigation · lighter layout")

nav_section = st.sidebar.selectbox("Area", list(PAGE_GROUPS.keys()))
_pages_here = PAGE_GROUPS[nav_section]
_page_ids = [pid for _lbl, pid in _pages_here]
_label_for = {pid: lbl for lbl, pid in _pages_here}

try:
    selected_page = st.sidebar.radio(
        "Page",
        options=_page_ids,
        index=0,
        format_func=lambda pid: _label_for.get(pid, pid),
        horizontal=len(_page_ids) <= 4,
    )
except TypeError:
    selected_page = st.sidebar.radio(
        "Page",
        options=_page_ids,
        index=0,
        format_func=lambda pid: _label_for.get(pid, pid),
    )

with st.sidebar.expander(f"What · {_label_for[selected_page]}", expanded=False):
    st.markdown(PAGE_SETUP_HINT.get(selected_page, "_No notes._"))

# System mode badge
try:
    mode = __import__("core.system_mode", fromlist=["get_system_mode"]).get_system_mode()
    if str(mode).upper() in ("LIVE",):
        st.sidebar.error("⚠️ LIVE MODE — verify execution guards")
    else:
        st.sidebar.success("🧪 TEST mode (.env SYSTEM_MODE)")
except Exception:
    st.sidebar.info("Mode: unknown")

st.sidebar.markdown("---")
st.sidebar.caption(f"Refresh UTC · {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
st.sidebar.caption("No full-app auto refresh (manual / source-driven view)")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — SYSTEM OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════

if selected_page == "1. System Overview":
    st.title("System Overview")
    st.markdown(
        "**Operational truth** for pulse + event-bus subscribers comes from **Redis** while "
        "`python run.py` is running. This Streamlit process only **monitors** — it does not "
        "start MT5 ingestion or subscriptions."
    )

    from core.config import describe_mt5_feed_readiness
    from dashboard.runtime_status import (
        build_component_overview_table,
        feed_operational_hints,
        redis_pulse_snapshot,
        startup_control_tower,
    )

    feed_report = describe_mt5_feed_readiness()
    pulse_snap, pulse_snap_err = redis_pulse_snapshot()

    if pulse_snap_err:
        st.warning(f"Could not read Redis pulse keys ({pulse_snap_err}). Is Redis up and `.env` loaded?")
    if not feed_report["ok"]:
        st.error("MT5 symbols missing — pulse cannot publish ticks until `.env` is fixed.")

    tower = startup_control_tower(feed_report, pulse_snap)
    st.markdown("---")
    st.subheader("Startup Control Tower")
    c0, c1, c2, c3, c4 = st.columns(5)
    c0.metric("Overall", "✅ ACTIVE" if tower["overall_ok"] else "⚠️ DEGRADED")
    c1.metric("Ingestion", "OK" if tower["ingestion_ok"] else "Check")
    c2.metric("Engine Subs", "OK" if tower["engine_ok"] else "Check")
    c3.metric("Pulse", tower["pulse_status"])
    c4.metric("DuckDB", f"{tower['duck_mode']}/{tower['duck_status']}")
    if tower["tick_age_s"] is not None:
        st.caption(f"Last tick age: {tower['tick_age_s']:.0f}s")
    if tower["duck_reason"]:
        st.caption(f"DuckDB note: {tower['duck_reason']}")
    if tower["summary"] != "LIVE FEED ACTIVE":
        st.warning(f"Status: {tower['summary']}")
    st.markdown("**Data source path (runtime truth):**")
    for src, status in tower["data_path"]:
        st.markdown(f"- `{src}` -> **{status}**")

    with st.expander("Market feed & engine checklist", expanded=not feed_report["ok"] or bool(pulse_snap_err)):
        for line in feed_operational_hints(feed_report, pulse_snap):
            st.markdown(f"- {line}")

    col1, col2, col3, col4 = st.columns(4)

    # Storage service
    storage = _safe_import("services.storage_service.storage")
    if storage:
        stats = storage.get_stats()
        with col1:
            st.metric("SQLite", f"{stats.get('sqlite_size_mb', 0):.1f} MB",
                      f"avg {stats.get('sqlite_avg_ms', 0):.1f}ms")
        with col2:
            db_ok = stats.get("duckdb_available", False)
            st.metric("DuckDB", "Online" if db_ok else "Offline",
                      f"{stats.get('duckdb_size_mb', 0):.1f} MB")
    else:
        with col1:
            st.metric("SQLite", "Unavailable")
        with col2:
            st.metric("DuckDB", "Unavailable")

    # State store
    state = _safe_import("core.state_store.state")
    if state:
        snap = state.snapshot()
        with col3:
            ks = snap.get("kill_switch_active", False)
            st.metric("Kill Switch", "🚨 ACTIVE" if ks else "✅ OK")
        with col4:
            st.metric("Equity", f"${snap.get('equity', 0):,.2f}")
    else:
        with col3:
            st.metric("Kill Switch", "N/A")
        with col4:
            st.metric("Equity", "N/A")

    st.markdown("---")

    st.subheader("Component status — Redis registry + local imports")
    try:
        import pandas as pd

        st.dataframe(pd.DataFrame(build_component_overview_table()), use_container_width=True)
    except Exception as exc:
        st.error(f"Could not render component table: {exc}")

    st.markdown("---")
    st.subheader("Worker threads (Redis)")
    try:
        import pandas as pd
        from core import system_registry as reg

        worker_rows = [reg.get_system_status(name) for name in reg.WORKER_SYSTEMS]
        st.dataframe(pd.DataFrame(worker_rows), use_container_width=True)
    except Exception as exc:
        st.info(f"Worker registry unavailable ({exc})")

    # Event bus stats (this interpreter — usually idle unless something subscribed here)
    st.markdown("---")
    st.subheader("Event Bus (this Streamlit process)")
    bus = _safe_import("core.event_bus.bus")
    if bus:
        diag = bus.get_diagnostics()
        c1, c2, c3 = st.columns(3)
        c1.metric("Events Published", diag.get("publish_count", 0))
        c2.metric("Handler Failures", diag.get("failure_count", 0))
        c3.metric("Active Subscribers", diag.get("subscriber_count", 0))
        st.caption(
            "Subscriber counts here stay near zero unless code in this process calls "
            "`bus.subscribe`. Live subscribers run inside `python run.py`."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — LIVE TRADING
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "2. Live Trading":
    st.title("Live Trading")

    state = _safe_import("core.state_store.state")
    if not state:
        st.error("State store unavailable")
        st.stop()

    snap = state.snapshot()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Equity",       f"${snap.get('equity', 0):,.2f}")
    c2.metric("Daily DD",     f"{snap.get('daily_dd_pct', 0):.2f}%",
              delta_color="inverse")
    c3.metric("Open Trades",  snap.get("open_trade_count", 0))
    c4.metric("Trades Today", snap.get("daily_trade_count", 0))
    c5.metric("Kill Switch",  "🚨 ON" if snap.get("kill_switch_active") else "✅ OFF")

    st.markdown("---")

    # Current regime
    regime_det = _safe_import("systems.intelligence.regime_detector.regime_detector")
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Current Regime")
        if regime_det:
            r = regime_det.get_current_regime()
            if r:
                st.markdown(f"**Regime:** `{r.regime.value}`")
                st.markdown(f"**ADX:** {r.adx:.1f}  |  **ATR:** {r.atr:.2f}")
                st.markdown(f"**Confidence:** {r.confidence:.0%}")
                st.markdown(f"**Tradeable:** {'✅ Yes' if r.is_tradeable() else '❌ No'}")
            else:
                st.info("No regime data yet — waiting for first H1 candle")
        else:
            st.info("Regime detector not running")

    with col_b:
        st.subheader("Current Session")
        sf = _safe_import("systems.intelligence.session_filter.session_filter")
        if sf:
            stats = sf.get_stats()
            st.markdown(f"**Session:** `{stats.get('current_session', 'N/A')}`")
            st.markdown(f"**Score:** {stats.get('session_score', 0):.0%}")
            st.markdown(f"**Tradeable:** {'✅ Yes' if stats.get('is_tradeable') else '❌ No'}")
        else:
            st.info("Session filter not running")

    st.markdown("---")
    st.subheader("Today's Signals")
    try:
        from repositories.signal_repository import SignalRepository
        from services.storage_service import storage as _storage
        repo = SignalRepository(_storage)
        approved = repo.get_today_approved()
        blocked  = repo.get_blocked_today()
        st.write(f"Approved: {len(approved)} | Blocked: {len(blocked)}")
        if approved:
            import pandas as pd
            st.dataframe(pd.DataFrame(approved)[["timestamp", "direction", "entry_price", "score", "lot_size"]])
    except Exception as e:
        st.info(f"No signal data yet ({e})")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — RISK CENTER
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "3. Risk Center":
    st.title("Risk Center")

    state = _safe_import("core.state_store.state")
    if state:
        snap = state.snapshot()
        dd  = snap.get("daily_dd_pct", 0)
        eq  = snap.get("equity", 0)
        ks  = snap.get("kill_switch_active", False)

        st.subheader("Drawdown Gauges")
        c1, c2 = st.columns(2)
        with c1:
            st.metric("Daily DD", f"{dd:.2f}%", delta_color="inverse")
            daily_limit = 4.0
            pct_to_limit = min(dd / daily_limit * 100, 100) if daily_limit > 0 else 0
            st.progress(int(pct_to_limit), text=f"{pct_to_limit:.0f}% of daily limit ({daily_limit}%)")
        with c2:
            total_dd = snap.get("total_dd_pct", 0)
            st.metric("Total DD", f"{total_dd:.2f}%", delta_color="inverse")
            total_limit = 10.0
            pct_total = min(total_dd / total_limit * 100, 100) if total_limit > 0 else 0
            st.progress(int(pct_total), text=f"{pct_total:.0f}% of total limit ({total_limit}%)")

    st.markdown("---")
    st.subheader("Kill Switch Control")

    ks_module = _safe_import("risk.kill_switch.kill_switch")
    if ks_module:
        ks_stats = ks_module.get_stats()
        if ks_stats.get("active"):
            st.error(f"🚨 KILL SWITCH ACTIVE\nReason: {ks_stats.get('reason', '')}\nActivated: {ks_stats.get('activated_at', '')}")
            if st.button("⚠️ Manual Reset Kill Switch", type="secondary"):
                ks_module.deactivate("Manual reset from dashboard")
                st.success("Kill switch deactivated. Verify DD levels before trading!")
                st.rerun()
        else:
            st.success("✅ Kill switch inactive — trading allowed")
            if st.button("🚨 Emergency Stop (Activate Kill Switch)", type="primary"):
                ks_module.activate("Manual activation from dashboard", triggered_by="dashboard")
                st.warning("Kill switch activated!")
                st.rerun()

    st.markdown("---")
    st.subheader("Kill Switch History")
    try:
        from repositories.state_repository import StateRepository
        from services.storage_service import storage as _storage
        repo = StateRepository(_storage)
        history = repo.get_kill_switch_history(limit=10)
        if history:
            import pandas as pd
            df = pd.DataFrame(history)[["activated_at", "reason", "triggered_by", "deactivated_at", "active"]]
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No kill switch history")
    except Exception as e:
        st.info(f"Kill switch history unavailable: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — FUNDED RULES
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "4. Funded Rules":
    st.title("Funded Account Rules")
    st.markdown("Live compliance status against your prop firm rules.")

    try:
        from core import config
        funded = config.load("funded_rules")
        active_firm = funded.get("active_firm", "FTMO")
        firm_rules  = funded.get("firms", {}).get(active_firm, {})

        st.subheader(f"Active Firm: {active_firm}")
        st.json(firm_rules)

        state = _safe_import("core.state_store.state")
        if state:
            snap     = state.snapshot()
            eq       = snap.get("equity", 10000)
            daily_dd = snap.get("daily_dd_pct", 0)
            total_dd = snap.get("total_dd_pct", 0)

            daily_limit = firm_rules.get("daily_dd_pct", 5.0)
            total_limit = firm_rules.get("max_dd_pct", 10.0)

            st.markdown("---")
            st.subheader("Compliance Check")
            col1, col2 = st.columns(2)
            with col1:
                daily_ok = daily_dd < daily_limit
                st.metric(
                    "Daily DD",
                    f"{daily_dd:.2f}% / {daily_limit}%",
                    delta="✅ OK" if daily_ok else "❌ LIMIT BREACHED",
                    delta_color="normal" if daily_ok else "inverse",
                )
            with col2:
                total_ok = total_dd < total_limit
                st.metric(
                    "Total DD",
                    f"{total_dd:.2f}% / {total_limit}%",
                    delta="✅ OK" if total_ok else "❌ LIMIT BREACHED",
                    delta_color="normal" if total_ok else "inverse",
                )

    except Exception as e:
        st.warning(f"Could not load funded rules: {e}")
        st.info("Check config/funded_rules.yaml")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 5 — REGIME MONITOR
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "5. Regime Monitor":
    st.title("Regime Intelligence Workstation")
    from dashboard.runtime_status import page_state_banner, regime_pipeline_health
    from core.bus import set_value
    from regime.regime_framework import REGIME_CATEGORIES, OPERATING_QUADRANTS
    from regime.regime_statistics import transition_frequencies, regime_occurrences
    from regime.runtime_config import REGIME_PARAM_SPECS, load_regime_runtime_config
    from repositories.regime_repository import RegimeRepository
    from services.storage_service import storage as _storage
    import json
    import pandas as pd

    regime_det = _safe_import("systems.intelligence.regime_detector.regime_detector")

    # ── Top Bar — Global Controls ─────────────────────────────────────────────
    lookback_options = {
        "1 day": 1 / 365,
        "3 days": 3 / 365,
        "1 week": 7 / 365,
        "2 weeks": 14 / 365,
        "1 month": 30 / 365,
        "2 months": 60 / 365,
        "3 months": 90 / 365,
        "6 months": 182 / 365,
        "1 year": 1.0,
        "2 years": 2.0,
        "3 years": 3.0,
        "5 years": 5.0,
        "10 years": 10.0,
        "custom date range": -1.0,
    }
    presets_dir = _ENGINE_ROOT / "config" / "regime" / "presets"
    presets_dir.mkdir(parents=True, exist_ok=True)

    top1, top2, top3, top4, top5, top6, top7, top8 = st.columns([1.1, 1, 1.3, 1.5, 1.1, 1.1, 1.1, 1.1])
    with top1:
        selected_symbol = st.selectbox("Symbol", ["XAUUSD"], index=0)
    with top2:
        selected_tf = st.selectbox("Timeframe", ["H1", "M30", "M15"], index=0)
    with top3:
        lb_label = st.selectbox("Lookback", list(lookback_options.keys()), index=8)
    with top4:
        live_mode = st.selectbox("Mode", ["Live", "Historical"], index=0)
    with top5:
        refresh_mode = st.selectbox("Refresh", ["Manual", "Auto"], index=0)
    with top6:
        preset_name = st.text_input("Preset", value="default", label_visibility="visible")
    with top7:
        if st.button("Save Preset"):
            payload = {
                "symbol": selected_symbol,
                "timeframe": selected_tf,
                "lookback_label": lb_label,
                "mode": live_mode,
                "refresh": refresh_mode,
            }
            with open(presets_dir / f"{preset_name}.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            st.success(f"Preset saved: {preset_name}")
    with top8:
        preset_files = sorted([p.stem for p in presets_dir.glob("*.json")])
        chosen = st.selectbox("Load preset", ["(none)"] + preset_files, index=0)
        if chosen != "(none)" and st.button("Load"):
            with open(presets_dir / f"{chosen}.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            st.session_state["regime_preset_loaded"] = data
            st.rerun()

    loaded_preset = st.session_state.get("regime_preset_loaded")
    if loaded_preset:
        st.caption(
            f"Preset loaded: {loaded_preset.get('symbol')} | {loaded_preset.get('timeframe')} | "
            f"{loaded_preset.get('lookback_label')} | {loaded_preset.get('mode')}"
        )

    if lb_label == "custom date range":
        d1, d2 = st.columns(2)
        with d1:
            start_date = st.date_input("Start date")
        with d2:
            end_date = st.date_input("End date")
        days = max(1, (end_date - start_date).days)
        lookback_years = round(days / 365.0, 4)
    else:
        lookback_years = lookback_options[lb_label]

    try:
        set_value("regime:lookback_years", float(lookback_years), silent=True)
    except Exception:
        pass
    if refresh_mode == "Auto":
        st.caption("Auto refresh enabled for workstation view.")

    # ── Shared runtime context ────────────────────────────────────────────────
    rp = regime_pipeline_health(selected_symbol, selected_tf)
    checks = {
        "mt5_connected": rp["mt5_connection"] == "connected",
        "pulse_running": rp["pulse_status"] == "RUNNING",
        "sanitizer_running": rp["sanitizer_status"] == "RUNNING",
        "hub_running": rp["hub_status"] == "RUNNING",
        "regime_running": rp["regime_status"] == "RUNNING",
    }
    waiting_reason = ""
    if selected_tf != "H1":
        waiting_reason = f"timeframe {selected_tf} is monitor-only; detector classifies on H1 candles."
    elif rp["candle_count"] < rp["required_candles"]:
        waiting_reason = (
            f"insufficient H1 candles ({rp['candle_count']}/{rp['required_candles']}); "
            f"last_candle={rp['last_candle'] or 'N/A'}"
        )
    fallback_reason = ""
    if rp["duck_mode"] != "RW":
        fallback_reason = f"DuckDB mode {rp['duck_mode']}/{rp['duck_status']}"
    banner = page_state_banner(
        page_name="Regime Monitor",
        runtime_source="Redis registry + DuckDB candle history",
        checks=checks,
        waiting_reason=waiting_reason,
        fallback_reason=fallback_reason,
    )

    r = regime_det.get_current_regime() if regime_det else None
    runtime_cfg = load_regime_runtime_config()

    @st.cache_data(ttl=120)
    def _cached_hist(symbol: str, tf: str, years: float):
        repo_local = RegimeRepository(_storage)
        return repo_local.get_history(symbol, tf, limit=min(5000, max(200, int(years * 365 * 24))))

    @st.cache_data(ttl=120)
    def _cached_dist(symbol: str, tf: str, years: float):
        repo_local = RegimeRepository(_storage)
        return repo_local.get_regime_distribution_by_years(symbol, tf, years)

    @st.cache_data(ttl=120)
    def _cached_perf(years: float):
        repo_local = RegimeRepository(_storage)
        return repo_local.get_regime_performance(years)

    hist = _cached_hist(selected_symbol, selected_tf, float(lookback_years))
    df = pd.DataFrame(hist) if hist else pd.DataFrame()

    # ── Section 1 — Current Regime Hero ───────────────────────────────────────
    st.markdown("---")
    st.subheader("Section 1 — Current Regime Panel")
    hero1, hero2, hero3 = st.columns([2, 1.2, 1.2])
    with hero1:
        label = getattr(r, "regime_label", "UNAVAILABLE") if r else "UNAVAILABLE"
        confidence = f"{getattr(r, 'confidence', 0.0):.0%}" if r else "N/A"
        st.markdown(f"### {label}")
        st.markdown(f"**Confidence:** {confidence}")
        st.markdown(
            f"**Transition:** {getattr(r, 'transition_state', 'N/A') if r else 'N/A'}  \n"
            f"**Status:** {banner['state']}"
        )
    with hero2:
        st.metric("Trend Strength", getattr(r, "trend_direction", "N/A") if r else "N/A")
        st.metric("Volatility", "HIGH" if (r and getattr(r, "atr_percentile", 0) >= 80) else "NORMAL")
        st.metric("Momentum", f"RSI {getattr(r, 'rsi', 0.0):.1f}" if r else "N/A")
    with hero3:
        st.metric("Liquidity State", getattr(r, "volume_signal", "N/A") if r else "N/A")
        st.metric("Session State", getattr(r, "session_label", "N/A") if r else "N/A")
        st.metric("Tradeable", "YES" if (r and r.is_tradeable()) else "NO")
    if banner["state"] == "RUNNING":
        st.success(banner["message"])
    elif banner["state"] == "WAITING":
        st.info(banner["message"])
    elif banner["state"] in ("DEGRADED", "FALLBACK"):
        st.warning(banner["message"])
    else:
        st.error(banner["message"])
    st.caption(f"Runtime source: {banner['runtime_source']}")

    # ── Section 2 — Probability Engine ────────────────────────────────────────
    st.markdown("---")
    st.subheader("Section 2 — Probability Engine")
    probs = getattr(r, "probabilities", {}) if r else {}
    if probs:
        p_df = pd.DataFrame(
            [{"Regime": k, "Probability": float(v)} for k, v in probs.items()]
        ).sort_values("Probability", ascending=False)
        left, right = st.columns([1.4, 1])
        with left:
            st.dataframe(p_df, use_container_width=True)
            for row in p_df.head(8).itertuples(index=False):
                st.progress(min(100, int(row.Probability * 100)), text=f"{row.Regime}: {row.Probability:.0%}")
        with right:
            try:
                import altair as alt
                donut = (
                    alt.Chart(p_df.head(8))
                    .mark_arc(innerRadius=60)
                    .encode(theta=alt.Theta(field="Probability", type="quantitative"), color=alt.Color(field="Regime", type="nominal"))
                    .properties(height=280)
                )
                st.altair_chart(donut, use_container_width=True)
            except Exception:
                st.bar_chart(p_df.set_index("Regime")["Probability"])
    else:
        st.info("Probability engine waiting for first valid classification.")

    # ── Section 3 — Classifier Grid ───────────────────────────────────────────
    st.markdown("---")
    st.subheader("Section 3 — Classifier Grid")
    c1, c2, c3 = st.columns(3)
    c4, c5 = st.columns(2)

    def _param_row(card, title, section, key, runtime_value):
        meta = REGIME_PARAM_SPECS.get(section, {}).get(key)
        if not meta:
            return
        current = runtime_cfg.get(section, {}).get(key, meta["default"])
        card.caption(
            f"{title} | Current: {current} | Allowed: {meta['min']}-{meta['max']} | "
            f"Recommended: {meta['recommended'][0]}-{meta['recommended'][1]} | Runtime: {runtime_value if runtime_value is not None else 'N/A'}"
        )

    with c1:
        st.markdown("**ADX Classifier**")
        st.metric("Current ADX", f"{getattr(r, 'adx', 0.0):.1f}" if r else "N/A")
        st.metric("Trend Class", getattr(r, "regime_label", "N/A") if r else "N/A")
        _param_row(st, "Weak threshold", "adx", "weak_trend_threshold", getattr(r, "adx", None) if r else None)
        _param_row(st, "Strong threshold", "adx", "strong_trend_threshold", getattr(r, "adx", None) if r else None)
    with c2:
        st.markdown("**ATR Classifier**")
        st.metric("ATR", f"{getattr(r, 'atr', 0.0):.2f}" if r else "N/A")
        st.metric("ATR Percentile", f"{getattr(r, 'atr_percentile', 0.0):.0f}" if r else "N/A")
        st.metric("Expansion", getattr(r, "transition_state", "N/A") if r else "N/A")
        _param_row(st, "High vol %", "atr", "high_vol_percentile", getattr(r, "atr_percentile", None) if r else None)
    with c3:
        st.markdown("**RSI / Momentum**")
        st.metric("RSI", f"{getattr(r, 'rsi', 0.0):.1f}" if r else "N/A")
        st.metric("Momentum", "Overbought/Oversold" if (r and (r.rsi >= 70 or r.rsi <= 30)) else "Neutral")
        _param_row(st, "Overbought", "rsi", "overbought", getattr(r, "rsi", None) if r else None)
        _param_row(st, "Oversold", "rsi", "oversold", getattr(r, "rsi", None) if r else None)
    with c4:
        st.markdown("**Structure**")
        st.metric("Structure Label", getattr(r, "structure_label", "N/A") if r else "N/A")
        _param_row(st, "Breakout sensitivity", "structure", "breakout_sensitivity", None)
        _param_row(st, "Consolidation length", "structure", "consolidation_length", rp.get("candle_count"))
    with c5:
        st.markdown("**Session / Volume**")
        st.metric("Session", getattr(r, "session_label", "N/A") if r else "N/A")
        st.metric("Volume Signal", getattr(r, "volume_signal", "N/A") if r else "N/A")
        _param_row(st, "London start IST", "session", "london_start_hour_ist", None)
        _param_row(st, "Spike multiplier", "volume", "spike_multiplier", None)

    # ── Section 4/5 — Explorer + Historical Analytics ────────────────────────
    st.markdown("---")
    st.subheader("Section 4 — Regime Explorer")
    explorer_tabs = st.tabs(["Trend", "Mean Reversion", "Wyckoff", "ICT/Order Flow", "Macro", "All Categories"])
    cat_map = {
        "Trend": "Trend",
        "Mean Reversion": "Mean Reversion",
        "Wyckoff": "Wyckoff",
        "ICT/Order Flow": "ICT/Order Flow",
        "Macro": "Macro/Central Bank",
    }
    for idx, tab_name in enumerate(["Trend", "Mean Reversion", "Wyckoff", "ICT/Order Flow", "Macro"]):
        with explorer_tabs[idx]:
            labels = REGIME_CATEGORIES.get(cat_map[tab_name], [])
            st.markdown(", ".join(labels) if labels else "No categories configured.")
    with explorer_tabs[5]:
        for cat, labels in REGIME_CATEGORIES.items():
            st.markdown(f"**{cat}**: {', '.join(labels[:8])}{' ...' if len(labels) > 8 else ''}")

    st.subheader("Section 5 — Historical Regime Analytics")
    if not df.empty:
        occ = regime_occurrences(hist)
        selected_regime = st.selectbox(
            "Click a regime to inspect historical analytics",
            sorted(occ.keys(), key=lambda x: occ.get(x, 0), reverse=True),
        )
        sub = df[(df["regime_label"] == selected_regime) if "regime_label" in df.columns else (df["regime"] == selected_regime)]
        o1, o2, o3, o4, o5, o6 = st.columns(6)
        o1.metric("Occurrences", len(sub))
        o2.metric("Avg Duration", f"{sub['candles_used'].mean():.1f} bars" if ("candles_used" in sub.columns and not sub.empty) else "N/A")
        o3.metric("Avg Move Proxy", f"{sub['atr'].mean():.2f}" if ("atr" in sub.columns and not sub.empty) else "N/A")
        perf = _cached_perf(float(lookback_years))
        perf_df = pd.DataFrame(perf) if perf else pd.DataFrame()
        row = perf_df[perf_df["regime"] == selected_regime] if (not perf_df.empty and "regime" in perf_df.columns) else pd.DataFrame()
        o4.metric("Win Rate", f"{row['win_rate_pct'].iloc[0]:.1f}%" if not row.empty else "N/A")
        o5.metric("PF", f"{row['profit_factor'].iloc[0]:.2f}" if not row.empty else "N/A")
        o6.metric("DD", f"{row['max_drawdown'].iloc[0]:.2f}" if not row.empty else "N/A")

        st.markdown("**Frequency / Duration / Performance**")
        dcol1, dcol2 = st.columns(2)
        with dcol1:
            dist = _cached_dist(selected_symbol, selected_tf, float(lookback_years))
            st.bar_chart(dist if dist else {})
        with dcol2:
            if "confidence" in sub.columns and "time" in sub.columns:
                chart_df = sub[["time", "confidence"]].copy().sort_values("time")
                chart_df = chart_df.set_index("time")
                st.line_chart(chart_df)

        if not perf_df.empty:
            st.markdown("**Best Strategies by Regime**")
            st.dataframe(perf_df, use_container_width=True)
            bs1, bs2, bs3 = st.columns(3)
            bs1.metric("Best Session", sub["session"].mode().iloc[0] if ("session" in sub.columns and not sub.empty) else "N/A")
            bs2.metric("Best Timeframe", selected_tf)
            bs3.metric("Best Symbol", selected_symbol)
    else:
        st.info("No regime history available for selected context.")

    # ── Section 6 — Transition Engine ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("Section 6 — Transition Engine")
    st.markdown(f"**Current Transition:** {getattr(r, 'transition_state', 'N/A') if r else 'N/A'}")
    trans = transition_frequencies(hist) if hist else {}
    t1, t2 = st.columns(2)
    with t1:
        st.bar_chart(trans if trans else {})
    with t2:
        if not df.empty and "transition_state" in df.columns and "regime_label" in df.columns:
            m = pd.crosstab(df["transition_state"], df["regime_label"])
            st.dataframe(m, use_container_width=True)
        else:
            st.info("Transition matrix available after enough history.")

    # ── Section 7 — Strategy Orchestration ────────────────────────────────────
    st.markdown("---")
    st.subheader("Section 7 — Strategy Orchestration")
    allowed = getattr(r, "allowed_strategies", []) if r else []
    blocked = [s for s in ["alpha_breakout", "alpha_pullback", "alpha_sweep"] if s not in set(allowed)]
    s1, s2 = st.columns(2)
    with s1:
        st.success("Allowed:\n" + ("\n".join([f"- {x}" for x in allowed]) if allowed else "- none"))
    with s2:
        st.warning("Blocked:\n" + ("\n".join([f"- {x}" for x in blocked]) if blocked else "- none"))
    if r and getattr(r, "mapping_reason", ""):
        st.caption(f"Mapping rationale: {r.mapping_reason}")

    # ── Section 8 — Dependency Health ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("Section 8 — Dependency Health")
    tick_age_txt = f"{rp['last_tick_age_s']:.0f}s" if rp["last_tick_age_s"] is not None else "N/A"
    dep_rows = [
        ("MT5", rp["mt5_connection"]),
        ("Pulse", rp["pulse_status"]),
        ("Sanitizer", rp["sanitizer_status"]),
        ("Hub", rp["hub_status"]),
        ("H1 Candles", f"{rp['candle_count']}/{rp['required_candles']}"),
        ("Regime Detector", rp["regime_status"]),
    ]
    st.markdown("`MT5 -> Pulse -> Sanitizer -> Hub -> H1 Candles -> Regime Detector`")
    st.dataframe([{"Stage": a, "State": b} for a, b in dep_rows], use_container_width=True)
    st.caption(f"Last tick age: {tick_age_txt} | Storage mode: {rp['duck_mode']}/{rp['duck_status']}")

    # ── Section 9 — Advanced Config (collapsible) ─────────────────────────────
    with st.expander("Section 9 — Advanced Config (quick controls)", expanded=False):
        st.caption("Full editable ranges are available on page `15. Config Editor` -> `Regime Config Lab`.")
        st.markdown(
            "- ADX thresholds\n"
            "- ATR percentile bands\n"
            "- Probability weights\n"
            "- Validator persistence/confidence/cooldown\n"
            "- Strategy multipliers"
        )

    # ── Section 10 — Backtest / Test Buttons ──────────────────────────────────
    st.markdown("---")
    st.subheader("Section 10 — Analyze / Replay / Compare")
    b1, b2, b3, b4, b5 = st.columns(5)
    if b1.button("Analyze Regime"):
        set_value("regime:analysis_request", {"symbol": selected_symbol, "tf": selected_tf, "lookback": lb_label}, silent=True)
        st.info("Regime analysis request queued.")
    if b2.button("Replay Regime"):
        set_value("regime:replay_request", {"symbol": selected_symbol, "tf": selected_tf, "lookback": lb_label}, silent=True)
        st.info("Regime replay request queued.")
    if b3.button("Backtest Strategy"):
        set_value("regime:backtest_request", {"symbol": selected_symbol, "tf": selected_tf, "lookback": lb_label}, silent=True)
        st.info("Backtest hook signal queued.")
    if b4.button("Compare Settings"):
        set_value("regime:compare_settings_request", {"symbol": selected_symbol, "tf": selected_tf}, silent=True)
        st.info("Compare-settings hook signal queued.")
    if b5.button("Run Historical Scan"):
        set_value("regime:historical_scan_request", {"symbol": selected_symbol, "tf": selected_tf, "lookback": lb_label}, silent=True)
        st.info("Historical scan hook signal queued.")

    if refresh_mode == "Auto":
        time.sleep(1.5)
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 6 — MARKET DATA
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "6. Market Data":
    st.title("Market Data Hub")

    from core.config import describe_mt5_feed_readiness
    from dashboard.runtime_status import feed_operational_hints, redis_pulse_snapshot
    from core.bus import get_value
    from core import system_registry as reg

    _feed_rep = describe_mt5_feed_readiness()
    _snap_md, _snap_md_err = redis_pulse_snapshot()
    with st.expander("Live MT5 feed status (from Redis / `.env`)", expanded=not _feed_rep["ok"]):
        if _snap_md_err:
            st.warning(_snap_md_err)
        for line in feed_operational_hints(_feed_rep, _snap_md):
            st.markdown(f"- {line}")
        st.caption(
            "Demo/live prices follow whichever account is logged into MetaTrader on this machine. "
            "CSV import works without MT5."
        )

    source_mode = st.radio(
        "Data source mode",
        ["Auto", "MT5 Live (Redis)", "DuckDB Stored"],
        horizontal=True,
        help="Choose which source to prioritize for market-data views on this page.",
    )
    c_ref1, c_ref2 = st.columns([1, 2])
    with c_ref1:
        if st.button("Refresh Values", type="primary"):
            st.rerun()
    with c_ref2:
        md_live_refresh = st.checkbox("Auto-refresh this page", value=True)
        md_refresh_secs = st.slider("Market refresh (sec)", 1, 10, 2)
    show_mt5 = source_mode in ("Auto", "MT5 Live (Redis)")
    show_duck = source_mode in ("Auto", "DuckDB Stored")

    # ── Live MT5 price action chart (Redis ticks, dashboard-safe) ──────────
    st.subheader("Live MT5 Price Action")
    symbols = list(_feed_rep.get("symbols") or []) if show_mt5 else []
    if symbols:
        hist_key = "md_tick_history"
        ts_key = "md_last_ts_by_symbol"
        if hist_key not in st.session_state:
            st.session_state[hist_key] = []
        if ts_key not in st.session_state:
            st.session_state[ts_key] = {}

        now_ts = time.time()
        for sym in symbols:
            bid = get_value(f"market:{sym}:bid", silent=True)
            ask = get_value(f"market:{sym}:ask", silent=True)
            ts = get_value(f"market:{sym}:timestamp", silent=True)
            if bid is None or ts is None:
                continue
            prev_ts = st.session_state[ts_key].get(sym)
            if prev_ts == ts:
                continue
            st.session_state[ts_key][sym] = ts

            tick_epoch = None
            try:
                tick_epoch = float(ts)
                if tick_epoch > 10_000_000_000:  # MT5 ms timestamp
                    tick_epoch = tick_epoch / 1000.0
            except Exception:
                tick_epoch = now_ts

            st.session_state[hist_key].append(
                {
                    "symbol": sym,
                    "time": datetime.fromtimestamp(tick_epoch, tz=timezone.utc),
                    "bid": float(bid),
                    "ask": float(ask) if ask is not None else None,
                }
            )

        # Keep memory bounded for long sessions.
        st.session_state[hist_key] = st.session_state[hist_key][-1200:]

        try:
            import pandas as pd

            hist_df = pd.DataFrame(st.session_state[hist_key])
        except Exception:
            hist_df = None

        if hist_df is not None and not hist_df.empty:
            latest_rows = []
            for sym in symbols:
                sym_df = hist_df[hist_df["symbol"] == sym].sort_values("time")
                if sym_df.empty:
                    continue
                latest = sym_df.iloc[-1]
                prev = sym_df.iloc[-2] if len(sym_df) > 1 else latest
                delta = float(latest["bid"]) - float(prev["bid"])
                latest_rows.append(
                    {
                        "Symbol": sym,
                        "Bid": float(latest["bid"]),
                        "Ask": float(latest["ask"]) if latest["ask"] is not None else None,
                        "Delta": delta,
                        "Direction": "UP" if delta > 0 else ("DOWN" if delta < 0 else "FLAT"),
                        "Color": "#16a34a" if delta > 0 else ("#dc2626" if delta < 0 else "#6b7280"),
                    }
                )

            if latest_rows:
                cols = st.columns(min(5, len(latest_rows)))
                for i, row in enumerate(latest_rows[: len(cols)]):
                    color = "green" if row["Direction"] == "UP" else ("red" if row["Direction"] == "DOWN" else "gray")
                    bid_txt = f"{row['Bid']:.5f}" if row.get("Bid") is not None else "N/A"
                    ask_txt = f"{row['Ask']:.5f}" if row.get("Ask") is not None else "N/A"
                    cols[i].markdown(
                        f"**{row['Symbol']}**  \n"
                        f"`Bid` {bid_txt}  \n"
                        f"`Ask` {ask_txt}  \n"
                        f":{color}[{row['Direction']} {row['Delta']:+.5f}]"
                    )
                st.markdown("**Live Market Values**")
                values_rows = []
                for row in latest_rows:
                    ts = get_value(f"market:{row['Symbol']}:timestamp", silent=True)
                    values_rows.append(
                        {
                            "Symbol": row["Symbol"],
                            "Bid": round(float(row["Bid"]), 5),
                            "Ask": round(float(row["Ask"]), 5) if row["Ask"] is not None else None,
                            "Spread": round(
                                (float(row["Ask"]) - float(row["Bid"])) if row["Ask"] is not None else 0.0, 5
                            ),
                            "Direction": row["Direction"],
                            "Delta": round(float(row["Delta"]), 5),
                            "MT5 Timestamp": ts,
                        }
                    )
                st.dataframe(values_rows, use_container_width=True)

            try:
                import altair as alt

                line = (
                    alt.Chart(hist_df)
                    .mark_line()
                    .encode(
                        x=alt.X("time:T", title="UTC time"),
                        y=alt.Y("bid:Q", title="Bid"),
                        color=alt.Color("symbol:N", title="Symbol"),
                        tooltip=["symbol:N", "time:T", "bid:Q", "ask:Q"],
                    )
                    .properties(height=240)
                    .interactive()
                )

                delta_df = pd.DataFrame(latest_rows)
                bars = (
                    alt.Chart(delta_df)
                    .mark_bar()
                    .encode(
                        x=alt.X("Symbol:N", sort=symbols),
                        y=alt.Y("Delta:Q", title="Last tick delta"),
                        color=alt.Color("Color:N", scale=None, legend=None),
                        tooltip=["Symbol:N", "Bid:Q", "Ask:Q", "Delta:Q", "Direction:N"],
                    )
                    .properties(height=140)
                )
                st.altair_chart(line, use_container_width=True)
                st.altair_chart(bars, use_container_width=True)
            except Exception:
                # Fallback when Altair is unavailable.
                pivot_df = hist_df.pivot_table(index="time", columns="symbol", values="bid", aggfunc="last")
                st.line_chart(pivot_df, use_container_width=True)
        else:
            st.info("Waiting for live tick samples to build MT5 price chart...")
    elif show_mt5:
        st.info("No MT5 symbols configured in `.env` (`MT5_SYMBOLS`).")
    else:
        st.info("MT5 live panel hidden by source mode (set to DuckDB Stored).")

    # Runtime-first visibility (ingestion and subscriptions), independent of DuckDB file lock state.
    st.markdown("---")
    st.subheader("Runtime Feed Health")
    pulse_row = reg.get_system_status("pulse")
    sanitizer_row = reg.get_system_status("tick_sanitizer")
    hub_row = reg.get_system_status("market_data_hub")
    q_row = reg.get_system_status("data_quality_monitor")
    tick_ts = get_value("pulse:last_tick_epoch", silent=True)
    tick_age_s = (time.time() - float(tick_ts)) if tick_ts else None
    c_rt1, c_rt2, c_rt3, c_rt4 = st.columns(4)
    c_rt1.metric("Pulse", str(pulse_row.get("status", "UNKNOWN")))
    c_rt2.metric("Sanitizer", str(sanitizer_row.get("status", "UNKNOWN")))
    c_rt3.metric("Hub", str(hub_row.get("status", "UNKNOWN")))
    c_rt4.metric("Last Tick Age (s)", f"{tick_age_s:.0f}" if tick_age_s is not None else "N/A")
    st.caption(
        "Ingestion state uses Redis/registry from `run.py`. If this is RUNNING, MT5 feed is active "
        "even when dashboard DuckDB reads are degraded."
    )
    symbols = list(_feed_rep.get("symbols") or [])
    if symbols:
        rows_sym = []
        now_ts = time.time()
        for sym in symbols:
            ts = get_value(f"market:{sym}:timestamp", silent=True)
            age = None
            try:
                ts_f = float(ts) if ts is not None else None
                if ts_f is not None:
                    # MT5 timestamp can be sec or msec.
                    if ts_f > 10_000_000_000:
                        ts_f = ts_f / 1000.0
                    age = max(0.0, now_ts - ts_f)
            except Exception:
                age = None
            rows_sym.append(
                {
                    "Symbol": sym,
                    "LastTickAgeSec": round(age, 1) if age is not None else None,
                    "Receiving": "Yes" if age is not None and age < 30 else "No/Delayed",
                }
            )
        st.dataframe(rows_sym, use_container_width=True)

    hub = _safe_import("systems.data.market_data_hub.hub")
    storage = _safe_import("services.storage_service.storage")

    if storage:
        st.markdown("---")
        st.subheader("Storage Access")
        sstats = storage.get_stats()
        c_st1, c_st2, c_st3, c_st4 = st.columns(4)
        c_st1.metric("Redis", "Connected" if not _snap_md_err else "Unavailable")
        c_st2.metric("SQLite", "Connected")
        c_st3.metric("DuckDB Mode", sstats.get("duckdb_mode", "UNKNOWN"))
        c_st4.metric("DuckDB Status", sstats.get("duckdb_status", "UNKNOWN"))
        if sstats.get("duckdb_mode") != "RW":
            st.warning(
                "Live feed active but DuckDB is not writable in this dashboard process. "
                "Running in fallback visibility mode."
            )
            reason = sstats.get("duckdb_reason")
            if reason:
                st.caption(f"DuckDB detail: {reason}")

    st.markdown("---")
    st.subheader("Data Coverage (DuckDB)")
    if not show_duck:
        st.info("DuckDB section hidden by source mode (set to MT5 Live).")
    elif storage:
        try:
            cov_rows = storage.execute_duckdb(
                """
                SELECT symbol, timeframe, COUNT(*) AS candles, MIN(time) AS first_candle, MAX(time) AS last_candle
                FROM candles
                GROUP BY symbol, timeframe
                ORDER BY symbol, timeframe
                """
            )
            if cov_rows:
                import pandas as pd

                st.dataframe(
                    pd.DataFrame(
                        cov_rows,
                        columns=["Symbol", "Timeframe", "Candles", "FirstCandle", "LastCandle"],
                    ),
                    use_container_width=True,
                )
            else:
                st.info(
                    "No candles stored yet in DuckDB. Live tick feed can still be active via Redis "
                    "(see Runtime Feed Health above)."
                )
        except Exception as exc:
            st.warning(f"DuckDB coverage read failed in dashboard process: {exc}")

    st.markdown("---")
    st.subheader("System Status (engine truth)")
    c_sy1, c_sy2, c_sy3 = st.columns(3)
    c_sy1.metric("Tick Sanitizer", str(sanitizer_row.get("status", "UNKNOWN")))
    c_sy2.metric("Market Data Hub", str(hub_row.get("status", "UNKNOWN")))
    c_sy3.metric("Quality Monitor", str(q_row.get("status", "UNKNOWN")))
    st.caption(
        "These statuses come from Redis registry written by `run.py`. "
        "Local Streamlit singleton counters are intentionally not used here to avoid misleading zeros."
    )

    st.markdown("---")
    st.subheader("Import Historical Data (CSV)")
    uploaded = st.file_uploader("Upload Dukascopy OHLCV CSV", type="csv")
    if uploaded and hub:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            tmp.write(uploaded.read())
            tmp_path = tmp.name
        symbol_input = st.text_input("Symbol", value="XAUUSD")
        tf_input     = st.selectbox("Timeframe", ["H1", "H4", "D1", "M15", "M5"])
        if st.button("Import"):
            with st.spinner("Importing..."):
                count = hub.import_dukascopy_csv(tmp_path, symbol_input, tf_input)
            st.success(f"Imported {count} candles for {symbol_input} {tf_input}")

    # Page-local refresh (only when Market Data page is selected).
    if md_live_refresh:
        time.sleep(md_refresh_secs)
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 7 — BACKTESTING
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "7. Backtesting":
    st.title("Backtesting")
    st.markdown("Run backtests using historical data from DuckDB.")

    col_a, col_b = st.columns([2, 1])
    with col_a:
        strategy_name = st.selectbox("Strategy", ["alpha_breakout", "alpha_sweep", "alpha_pullback"])
        symbol_bt = st.text_input("Symbol", value="XAUUSD")
        timeframe_bt = st.selectbox("Timeframe", ["H1", "H4", "M15"], index=0)
        spread_pips = st.slider("Spread (pips)", 0.1, 3.0, 0.3, 0.1)
        commission  = st.slider("Commission ($/lot)", 0.0, 10.0, 3.5, 0.5)

    with col_b:
        st.markdown("**Pass Criteria:**")
        st.markdown("✓ 200+ trades")
        st.markdown("✓ PF ≥ 1.2")
        st.markdown("✓ Sharpe ≥ 0.8")
        st.markdown("✓ Win rate 40-60%")
        st.markdown("✓ Max DD < 15%")

    if st.button("▶ Run Backtest", type="primary"):
        with st.spinner(f"Running backtest: {strategy_name}..."):
            try:
                if strategy_name == "alpha_breakout":
                    from strategies.alpha_breakout import AlphaBreakout
                    strat = AlphaBreakout()
                else:
                    from strategies.alpha_breakout import AlphaBreakout
                    strat = AlphaBreakout()
                    strat.name = strategy_name

                from systems.research.backtester import Backtester
                bt = Backtester(spread_pips=spread_pips, commission=commission)
                result = bt.run(strat, symbol=symbol_bt, timeframe=timeframe_bt)

                # Store in session state for display
                st.session_state["last_backtest"] = result
            except Exception as e:
                st.error(f"Backtest error: {e}")

    if "last_backtest" in st.session_state:
        result = st.session_state["last_backtest"]
        st.markdown("---")
        st.subheader("Results")
        status_icon = "✅" if result.passes_criteria else "❌"
        st.markdown(f"### {status_icon} {result.summary()}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Trades", result.total_trades)
        c2.metric("Profit Factor", f"{result.profit_factor:.2f}")
        c3.metric("Sharpe Ratio",  f"{result.sharpe_ratio:.2f}")
        c4.metric("Max DD",        f"{result.max_drawdown_pct:.1f}%")

        c5, c6, c7 = st.columns(3)
        c5.metric("Win Rate", f"{result.win_rate:.1f}%")
        c6.metric("Total P&L", f"${result.total_net_pnl:+,.2f}")
        c7.metric("Avg P&L", f"${result.avg_net_pnl:+.2f}")

        if result.failure_reasons:
            st.error("Failed criteria:\n" + "\n".join(result.failure_reasons))

        # Equity curve
        if result.equity_curve:
            import pandas as pd
            eq_df = pd.DataFrame(result.equity_curve, columns=["time", "equity"])
            st.line_chart(eq_df.set_index("time")["equity"])

        # Trade table
        if result.trades:
            st.subheader("Trade Log")
            import pandas as pd
            trades_df = pd.DataFrame(result.trades)
            st.dataframe(trades_df[["direction", "entry_price", "close_price",
                                     "close_reason", "net_pnl", "bars_open"]],
                         use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 8 — WALK FORWARD
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "8. Walk Forward":
    st.title("Walk-Forward Engine")
    st.markdown("Validates strategy on out-of-sample data it has never seen.")

    n_windows = st.slider("Number of OOS Windows", 2, 5, 3)
    if st.button("▶ Run Walk-Forward Test", type="primary"):
        with st.spinner("Running walk-forward..."):
            try:
                from strategies.alpha_breakout import AlphaBreakout
                from systems.research.walk_forward import WalkForwardEngine
                from systems.data.market_data_hub import hub

                candles = hub.get_candles("XAUUSD", "H1", n=2000)
                norm_data = []
                if hasattr(candles, "to_dict"):
                    candles = candles.reset_index()
                    norm_data = candles.to_dict("records")
                elif isinstance(candles, list):
                    norm_data = candles

                if not norm_data:
                    st.error("No candle data. Import historical CSV first (Market Data page).")
                else:
                    wf = WalkForwardEngine(n_windows=n_windows)
                    result = wf.run(AlphaBreakout(), norm_data)
                    st.session_state["last_wf"] = result
            except Exception as e:
                st.error(f"Walk-forward error: {e}")

    if "last_wf" in st.session_state:
        result = st.session_state["last_wf"]
        status = "✅ PASS" if result.passes else "❌ FAIL"
        st.markdown(f"### {status}")

        c1, c2, c3 = st.columns(3)
        c1.metric("Pass Rate",       f"{result.pass_rate:.0f}%")
        c2.metric("Avg OOS PF",      f"{result.avg_oos_pf:.2f}")
        c3.metric("Stability Score", f"{result.stability_score:.2f}")

        if result.windows:
            import pandas as pd
            rows = [{
                "Window": w.window_index,
                "IS PF":  f"{w.in_sample_pf:.2f}",
                "OOS PF": f"{w.out_sample_pf:.2f}",
                "Pass":   "✅" if w.passes else "❌"
            } for w in result.windows]
            st.dataframe(pd.DataFrame(rows), use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 9 — MONTE CARLO
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "9. Monte Carlo":
    st.title("Monte Carlo Simulation")
    st.markdown("Analyzes probability distribution of outcomes from randomized trade sequences.")

    n_sims = st.selectbox("Simulations", [1000, 5000, 10000], index=1)

    if "last_backtest" not in st.session_state:
        st.info("Run a backtest first (page 7) to get trade PnL data for Monte Carlo.")
    else:
        if st.button("▶ Run Monte Carlo", type="primary"):
            result = st.session_state["last_backtest"]
            trade_pnls = [t.get("net_pnl", 0) for t in result.trades]
            if not trade_pnls:
                st.error("No trades from backtest")
            else:
                with st.spinner(f"Running {n_sims:,} simulations..."):
                    from systems.research.monte_carlo import MonteCarlo
                    mc = MonteCarlo(n_simulations=n_sims)
                    mc_result = mc.run(trade_pnls)
                    st.session_state["last_mc"] = mc_result

    if "last_mc" in st.session_state:
        mc = st.session_state["last_mc"]
        passed, failures = mc.passes_criteria()
        st.markdown(f"### {'✅ PASS' if passed else '❌ FAIL'}")

        c1, c2, c3 = st.columns(3)
        c1.metric("Risk of Ruin", f"{mc.risk_of_ruin_pct:.1f}%",
                  delta="❌ DANGEROUS" if mc.risk_of_ruin_pct >= 5 else "✅ Safe",
                  delta_color="inverse" if mc.risk_of_ruin_pct >= 5 else "normal")
        c2.metric("Max DD (95th %ile)", f"{mc.max_dd_p95:.1f}%",
                  delta_color="inverse")
        c3.metric("Final Equity (50th %ile)", f"${mc.equity_p50:,.0f}")

        c4, c5, c6 = st.columns(3)
        c4.metric("Max DD Median", f"{mc.max_dd_p50:.1f}%")
        c5.metric("Max Losing Streak (P95)", mc.longest_losing_streak_p95)
        c6.metric("Avg Final Equity", f"${mc.avg_final_equity:,.0f}")

        if failures:
            st.error("Failed:\n" + "\n".join(failures))

        if mc.equity_curves:
            import pandas as pd
            st.subheader("100 Sample Equity Paths")
            max_len = max(len(c) for c in mc.equity_curves)
            chart_data = {}
            for i, curve in enumerate(mc.equity_curves[:20]):
                padded = curve + [curve[-1]] * (max_len - len(curve)) if curve else []
                chart_data[f"sim_{i}"] = padded
            if chart_data:
                df_chart = pd.DataFrame(chart_data)
                st.line_chart(df_chart)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 10 — JOURNAL
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "10. Journal":
    st.title("Trade Journal")

    try:
        from repositories.trade_repository import TradeRepository
        from services.storage_service import storage as _storage
        repo = TradeRepository(_storage)

        today_summary = repo.get_daily_summary()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Today's Trades", today_summary.get("total_trades", 0))
        c2.metric("Today's Wins",   today_summary.get("wins", 0))
        c3.metric("Today's P&L",    f"${today_summary.get('total_net_pnl', 0):+.2f}")
        c4.metric("Win Rate",       f"{today_summary.get('win_rate', 0):.1f}%")

        st.markdown("---")
        thirty_day = repo.get_performance_summary(days=30)
        if thirty_day:
            st.subheader("30-Day Performance")
            c5, c6, c7, c8 = st.columns(4)
            c5.metric("Trades",        thirty_day.get("total_trades", 0))
            c6.metric("Win Rate",      f"{thirty_day.get('win_rate', 0):.1f}%")
            c7.metric("Profit Factor", f"{thirty_day.get('profit_factor', 0):.2f}")
            c8.metric("Total P&L",     f"${thirty_day.get('total_net_pnl', 0):+.2f}")

        st.markdown("---")
        st.subheader("Trade History")
        page_num = st.number_input("Page", min_value=1, value=1)
        trades = repo.get_paginated(page=page_num, page_size=50)
        if trades:
            import pandas as pd
            df = pd.DataFrame(trades)
            cols_show = [c for c in ["open_time", "symbol", "direction", "volume",
                                      "fill_price", "close_price", "close_reason",
                                      "net_pnl", "strategy"] if c in df.columns]
            st.dataframe(df[cols_show], use_container_width=True)
        else:
            st.info("No trades yet")
    except Exception as e:
        st.warning(f"Journal data unavailable: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 11 — EXECUTION ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "11. Execution Analytics":
    st.title("Execution Analytics")

    profiler = _safe_import("execution.profiler.execution_profiler")
    if profiler:
        stats = profiler.get_stats()
        c1, c2, c3 = st.columns(3)
        c1.metric("Avg Slippage",  f"{stats.get('avg_slippage_pips', 0):.2f} pips")
        c2.metric("Avg Latency",   f"{stats.get('avg_latency_ms', 0):.0f} ms")
        c3.metric("Avg Spread",    f"{stats.get('avg_spread_pips', 0):.2f} pips")

        c4, c5 = st.columns(2)
        c4.metric("Total Fills",   stats.get("fill_count", 0))
        c5.metric("Quality Alerts", stats.get("alert_count", 0))

        st.markdown("---")
        st.subheader("Benchmarks")
        st.markdown("""
        | Metric | Excellent | Good | Poor |
        |--------|-----------|------|------|
        | Slippage | <0.1 pips | 0.1-0.5 pips | >1.0 pips |
        | Latency | <50ms | 50-200ms | >1000ms |
        | Spread (London) | <0.3 pips | 0.3-0.8 pips | >1.5 pips |
        """)
    else:
        st.info("Execution profiler not running")

    try:
        from services.storage_service import storage as _storage
        rows = _storage.execute_sqlite(
            "SELECT * FROM execution_fills ORDER BY id DESC LIMIT 100"
        )
        if rows:
            import pandas as pd
            df = pd.DataFrame([dict(r) for r in rows])
            st.subheader("Recent Fills")
            st.dataframe(df[["timestamp", "symbol", "slippage_pips",
                              "fill_latency_ms", "spread_at_fill"]] if "timestamp" in df.columns else df,
                         use_container_width=True)
    except Exception as e:
        st.info(f"Fill history: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 12 — EVENT BUS MONITOR
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "12. Event Bus Monitor":
    st.title("Event Bus Monitor")

    bus = _safe_import("core.event_bus.bus")
    if not bus:
        st.error("Event bus unavailable")
        st.stop()

    diag = bus.get_diagnostics()

    c1, c2, c3 = st.columns(3)
    c1.metric("Events Published",   diag.get("publish_count", 0))
    c2.metric("Handler Failures",   diag.get("failure_count", 0),
              delta_color="inverse" if diag.get("failure_count", 0) > 0 else "normal")
    c3.metric("Active Subscribers", diag.get("subscriber_count", 0))

    st.markdown("---")
    st.subheader("Subscribers by Event Type")
    subs = diag.get("subscribers", {})
    for etype, handlers in sorted(subs.items()):
        with st.expander(f"**{etype}** ({len(handlers)} handlers)"):
            for h in handlers:
                st.code(h)

    st.markdown("---")
    st.subheader("Recent Events (last 20)")
    recent = diag.get("recent_events", [])
    if recent:
        import pandas as pd
        df = pd.DataFrame(recent[::-1])  # newest first
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No events yet")

    st.markdown("---")
    st.subheader("Handler Performance")
    metrics = diag.get("metrics", {})
    perf_rows = []
    for etype, handlers in metrics.items():
        for hname, m in handlers.items():
            perf_rows.append({
                "Event Type": etype,
                "Handler":    hname,
                "Calls":      m.get("calls", 0),
                "Failures":   m.get("failures", 0),
                "Avg ms":     m.get("avg_ms", 0),
                "Success %":  m.get("success_rate", 100),
            })
    if perf_rows:
        import pandas as pd
        st.dataframe(pd.DataFrame(perf_rows), use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 13 — RECOVERY DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "13. Recovery Dashboard":
    st.title("Recovery Dashboard")

    try:
        from core.recovery_manager import get_last_recovery_report, get_restart_count
        report = get_last_recovery_report()
        restarts = get_restart_count()

        st.metric("Total Restarts", restarts)

        if report:
            st.markdown("---")
            st.subheader("Last Recovery Report")
            c1, c2, c3 = st.columns(3)
            c1.metric("Timestamp", report.get("timestamp", "N/A")[:19])
            c2.metric("Kill Switch Restored", "Yes" if report.get("kill_switch_was_active") else "No")
            c3.metric("Orphan Trades", len(report.get("orphan_trade_ids", [])))

            c4, c5 = st.columns(2)
            c4.metric("Reconciliation", report.get("reconciliation_status", "N/A"))
            c5.metric("Daily DD Rebuilt", f"{report.get('daily_dd_pct_restored', 0):.2f}%")

            if report.get("warnings"):
                st.warning("Warnings:\n" + "\n".join(report["warnings"]))
            if report.get("actions_taken"):
                st.info("Actions taken:\n" + "\n".join(report["actions_taken"]))
        else:
            st.info("No recovery report found.  Run the engine once to generate it.")
    except Exception as e:
        st.warning(f"Recovery data unavailable: {e}")

    st.markdown("---")
    try:
        from services.storage_service import storage as _storage
        rows = _storage.execute_sqlite(
            "SELECT * FROM recovery_log ORDER BY id DESC LIMIT 10"
        )
        if rows:
            import pandas as pd
            st.subheader("Recovery History")
            st.dataframe(pd.DataFrame([dict(r) for r in rows]), use_container_width=True)
    except Exception as e:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 14 — LOGS VIEWER
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "14. Logs Viewer":
    try:
        from dashboard.log_viewer import render_logs_page
        render_logs_page()
    except Exception as e:
        st.error(f"Could not load structured log viewer: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 15 — CONFIG EDITOR
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "15. Config Editor":
    st.title("Config Editor")
    st.markdown("Edit YAML configuration files and tune Regime Intelligence values safely.")
    tab_raw, tab_regime = st.tabs(["Raw YAML Editor", "Regime Config Lab"])

    with tab_raw:
        config_dir = _ENGINE_ROOT / "config"
        yaml_files = list(config_dir.glob("*.yaml"))

        if not yaml_files:
            st.error(f"No YAML files in {config_dir}")
        else:
            selected_cfg = st.selectbox("Config file", [f.name for f in yaml_files])
            cfg_path = config_dir / selected_cfg

            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    current_content = f.read()

                edited = st.text_area(
                    f"Editing: {selected_cfg}",
                    value=current_content,
                    height=360,
                )

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("💾 Save", type="primary"):
                        try:
                            import yaml
                            yaml.safe_load(edited)
                            with open(cfg_path, "w", encoding="utf-8") as f:
                                f.write(edited)
                            st.success(f"Saved {selected_cfg}")
                            try:
                                from core import config
                                config.reload()
                                st.info("Config cache reloaded")
                            except Exception:
                                pass
                        except Exception as e:
                            st.error(f"Invalid YAML: {e}")
                with col2:
                    if st.button("↩ Reload from File"):
                        st.rerun()
            except Exception as e:
                st.error(f"Could not read config file: {e}")

    with tab_regime:
        from regime.runtime_config import (
            REGIME_PARAM_SPECS,
            load_regime_runtime_config,
            save_runtime_config,
            validate_runtime_config,
            audit_change,
            reset_defaults,
            CONFIG_DIR as _REG_CFG_DIR,
            SNAPSHOT_DIR as _REG_SNAP_DIR,
        )
        from core.bus import set_value as _set_bus

        # Runtime metrics for visibility beside thresholds.
        regime_det = _safe_import("systems.intelligence.regime_detector.regime_detector")
        current_regime = regime_det.get_current_regime() if regime_det else None
        runtime_metrics = {
            "adx.weak_trend_threshold": getattr(current_regime, "adx", None),
            "adx.strong_trend_threshold": getattr(current_regime, "adx", None),
            "atr.low_vol_percentile": getattr(current_regime, "atr_percentile", None),
            "atr.high_vol_percentile": getattr(current_regime, "atr_percentile", None),
            "atr.chaotic_vol_percentile": getattr(current_regime, "atr_percentile", None),
            "rsi.overbought": getattr(current_regime, "rsi", None),
            "rsi.oversold": getattr(current_regime, "rsi", None),
            "volume.spike_multiplier": None,
            "structure.breakout_sensitivity": None,
            "validator.confidence_threshold": getattr(current_regime, "confidence", None),
            "validator.min_persistence_candles": getattr(current_regime, "bars_in_regime", None),
            "probability.probability_threshold": max((getattr(current_regime, "probabilities", {}) or {}).values(), default=None),
        }

        loaded_cfg = load_regime_runtime_config()
        if "regime_lab_working_cfg" not in st.session_state:
            st.session_state["regime_lab_working_cfg"] = loaded_cfg
        working_cfg = st.session_state["regime_lab_working_cfg"]

        st.markdown("### Regime UI Editor + Configurable Values")
        st.caption("Each value shows current runtime metric, threshold, allowed range, recommended range, default, and source.")

        coltop1, coltop2, coltop3, coltop4 = st.columns(4)
        with coltop1:
            if st.button("Reload From YAML"):
                st.session_state["regime_lab_working_cfg"] = load_regime_runtime_config()
                st.rerun()
        with coltop2:
            if st.button("Reset Safe Defaults"):
                st.session_state["regime_lab_working_cfg"] = reset_defaults()
                st.warning("Defaults loaded in editor. Save to apply.")
        with coltop3:
            snaps = sorted(list(_REG_SNAP_DIR.glob("*.yaml")), reverse=True)
            snap_name = st.selectbox("Rollback snapshot", ["(none)"] + [s.name for s in snaps], index=0)
            if snap_name != "(none)" and st.button("Load Snapshot"):
                import yaml
                with open(_REG_SNAP_DIR / snap_name, "r", encoding="utf-8") as f:
                    st.session_state["regime_lab_working_cfg"] = yaml.safe_load(f) or load_regime_runtime_config()
                st.info(f"Loaded snapshot {snap_name}")
                st.rerun()
        with coltop4:
            if st.button("Export Current Regime Config"):
                import yaml
                st.download_button(
                    "Download YAML",
                    data=yaml.safe_dump(working_cfg, sort_keys=False),
                    file_name="regime_config_export.yaml",
                    mime="application/x-yaml",
                )

        st.markdown("---")
        for section, params in REGIME_PARAM_SPECS.items():
            with st.expander(f"{section.upper()} Parameters", expanded=False):
                for name, meta in params.items():
                    key = f"{section}.{name}"
                    current_value = working_cfg.get(section, {}).get(name, meta["default"])
                    runtime_value = runtime_metrics.get(key)
                    st.markdown(f"**{name}**")
                    c1, c2, c3, c4 = st.columns([1.1, 1, 1, 1.4])
                    with c1:
                        st.caption(f"Runtime value: {runtime_value if runtime_value is not None else 'N/A'}")
                        st.caption(f"Source: config/runtime/UI override")
                    with c2:
                        st.caption(f"Allowed: {meta['min']} - {meta['max']}")
                        st.caption(f"Recommended: {meta['recommended'][0]} - {meta['recommended'][1]}")
                    with c3:
                        st.caption(f"Default: {meta['default']}")
                        st.caption(f"Warning limits: {meta['warning'][0]} - {meta['warning'][1]}")
                    with c4:
                        if isinstance(meta["default"], int):
                            v = st.number_input(
                                f"{key}",
                                min_value=int(meta["min"]),
                                max_value=int(meta["max"]),
                                value=int(current_value),
                                step=1,
                                key=f"regcfg_{key}",
                                label_visibility="collapsed",
                            )
                        else:
                            v = st.slider(
                                f"{key}",
                                min_value=float(meta["min"]),
                                max_value=float(meta["max"]),
                                value=float(current_value),
                                key=f"regcfg_{key}",
                                label_visibility="collapsed",
                            )
                        working_cfg.setdefault(section, {})[name] = v

        errs = validate_runtime_config(working_cfg)
        if errs:
            st.error("Validation errors:\n- " + "\n- ".join(errs))
        else:
            st.success("All configured values are within safe limits.")

        csave1, csave2, csave3 = st.columns(3)
        with csave1:
            if st.button("Save Regime Config", type="primary"):
                try:
                    old_cfg = load_regime_runtime_config()
                    save_runtime_config(working_cfg, source="ui_override")
                    audit_change(old_cfg, working_cfg, source="ui_override")
                    _set_bus("regime:config_last_updated_ts", time.time(), silent=True)
                    try:
                        from core import config
                        config.reload()
                    except Exception:
                        pass
                    st.success("Regime config saved and audit-tracked.")
                except Exception as e:
                    st.error(f"Save failed: {e}")
        with csave2:
            if st.button("Quick Retest Hook (1 week)"):
                _set_bus("regime:retest_request", {"range": "1 week", "ts": time.time()}, silent=True)
                st.info("Retest request signal posted. Connect this key in research runner.")
        with csave3:
            if st.button("Compare Working vs Runtime"):
                runtime_now = load_regime_runtime_config()
                diffs = []
                for sec, params in REGIME_PARAM_SPECS.items():
                    for pname in params:
                        a = runtime_now.get(sec, {}).get(pname)
                        b = working_cfg.get(sec, {}).get(pname)
                        if a != b:
                            diffs.append({"key": f"{sec}.{pname}", "runtime": a, "working": b})
                if diffs:
                    st.dataframe(diffs, use_container_width=True)
                else:
                    st.info("No differences.")

        st.markdown("---")
        st.caption(f"Regime config folder: {_REG_CFG_DIR}")
        st.caption("Change tracking logs old/new values with timestamp and source in logs/regime_config_audit.jsonl.")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 16 — STRATEGY BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "16. Strategy Builder":
    st.title("Strategy Builder")
    st.markdown("Enable/disable strategies and view their parameters.")

    strategies_info = []
    strategy_modules = [
        ("alpha_breakout", "strategies.alpha_breakout", "AlphaBreakout"),
    ]

    for name, module, cls_name in strategy_modules:
        try:
            mod = __import__(module, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
            strat = cls()
            info = strat.get_info()
            strategies_info.append(info)
        except Exception as e:
            strategies_info.append({"name": name, "error": str(e)})

    for info in strategies_info:
        with st.expander(f"**{info.get('name', 'unknown')}**"):
            if "error" in info:
                st.error(info["error"])
            else:
                st.write(f"Description: {info.get('description', '')}")
                st.write(f"Timeframe: {info.get('timeframe', 'H1')}")
                st.write(f"Symbol: {info.get('symbol', 'XAUUSD')}")
                st.write(f"Signal count: {info.get('signal_count', 0)}")
                enabled = info.get("enabled", True)
                st.write(f"Status: {'✅ Enabled' if enabled else '⛔ Disabled'}")

    st.markdown("---")
    st.subheader("Signal Blocking Summary (7 days)")
    try:
        from repositories.signal_repository import SignalRepository
        from services.storage_service import storage as _storage
        repo = SignalRepository(_storage)
        summary = repo.get_block_reason_summary(days=7)
        if summary:
            import pandas as pd
            st.dataframe(pd.DataFrame(summary), use_container_width=True)
        else:
            st.info("No blocked signals in last 7 days")
    except Exception as e:
        st.info(f"Signal data: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 17 — VPS HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "17. VPS Health":
    st.title("VPS Health")

    try:
        import psutil
        c1, c2, c3 = st.columns(3)
        c1.metric("CPU Usage",    f"{psutil.cpu_percent(interval=1):.1f}%")
        c2.metric("RAM Usage",    f"{psutil.virtual_memory().percent:.1f}%")
        c3.metric("Disk Usage",   f"{psutil.disk_usage('/').percent:.1f}%")

        # Process info
        st.markdown("---")
        st.subheader("Running Processes")
        procs = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            if "python" in proc.info["name"].lower():
                procs.append({
                    "PID":   proc.info["pid"],
                    "Name":  proc.info["name"],
                    "CPU%":  proc.info["cpu_percent"],
                    "RAM MB": round(proc.info["memory_info"].rss / 1e6, 1),
                })
        if procs:
            import pandas as pd
            st.dataframe(pd.DataFrame(procs), use_container_width=True)
    except ImportError:
        st.info("psutil not installed.  Run: pip install psutil")
    except Exception as e:
        st.warning(f"System metrics unavailable: {e}")

    st.markdown("---")
    st.subheader("Database Health")
    storage = _safe_import("services.storage_service.storage")
    if storage:
        stats = storage.get_stats()
        col1, col2 = st.columns(2)
        with col1:
            st.write("**SQLite**")
            st.write(f"Size: {stats.get('sqlite_size_mb', 0):.1f} MB")
            st.write(f"Avg latency: {stats.get('sqlite_avg_ms', 0):.1f} ms")
            st.write(f"Operations: {stats.get('sqlite_op_count', 0)}")
        with col2:
            st.write("**DuckDB**")
            st.write(f"Available: {stats.get('duckdb_available', False)}")
            st.write(f"Size: {stats.get('duckdb_size_mb', 0):.1f} MB")
            st.write(f"Avg latency: {stats.get('duckdb_avg_ms', 0):.1f} ms")

        row_counts = storage.get_table_row_counts()
        if row_counts:
            st.subheader("Table Row Counts")
            import pandas as pd
            st.dataframe(
                pd.DataFrame(list(row_counts.items()), columns=["Table", "Rows"]),
                use_container_width=True,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 18 — ALERTS CENTER
# ═══════════════════════════════════════════════════════════════════════════════

elif selected_page == "18. Alerts Center":
    st.title("Alerts Center")
    st.markdown("All system alerts across risk, data, execution, and intelligence systems.")

    bus = _safe_import("core.event_bus.bus")
    if bus:
        diag = bus.get_diagnostics()
        recent_events = diag.get("recent_events", [])
        alert_events  = [
            e for e in recent_events
            if e.get("event_type", "") in {
                "KILL_SWITCH", "DRAWDOWN_LIMIT", "DRAWDOWN_WARNING",
                "DATA_QUALITY_ALERT", "DATA_GAP_DETECTED", "TICK_REJECTED",
                "SYSTEM_ERROR", "HANDLER_FAILED", "ORPHAN_TRADE_FOUND",
                "SIGNAL_BLOCKED",
            }
        ]
        if alert_events:
            for evt in reversed(alert_events[-20:]):
                et = evt.get("event_type", "")
                ts = evt.get("timestamp", "")[:19]
                src = evt.get("source", "")
                if et in {"KILL_SWITCH", "DRAWDOWN_LIMIT", "SYSTEM_ERROR"}:
                    st.error(f"[{ts}] **{et}** from {src}")
                elif et in {"DRAWDOWN_WARNING", "DATA_QUALITY_ALERT", "SIGNAL_BLOCKED"}:
                    st.warning(f"[{ts}] **{et}** from {src}")
                else:
                    st.info(f"[{ts}] **{et}** from {src}")
        else:
            st.success("✅ No alerts — all systems normal")
    else:
        st.info("Event bus not running")

    st.markdown("---")
    st.subheader("Quality Monitor Alerts")
    quality = _safe_import("systems.data.data_quality_monitor.quality_monitor")
    if quality:
        report = quality.get_report()
        alerts = report.get("recent_alerts", [])
        if alerts:
            for alert in reversed(alerts[-10:]):
                st.warning(
                    f"[{alert.get('detected_at', '')[:19]}] "
                    f"**{alert.get('alert_type')}** {alert.get('symbol')}: {alert.get('detail')}"
                )
        else:
            st.success("No data quality alerts")

