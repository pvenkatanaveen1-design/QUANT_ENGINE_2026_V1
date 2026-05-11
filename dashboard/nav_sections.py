"""
Grouped navigation + short setup copy for the Streamlit dashboard.

Keeps dashboard_app.py thinner and documents what each page expects at runtime.
"""

from __future__ import annotations

# Section title → list of (short label, page id matching dashboard elif branches).
PAGE_GROUPS: dict[str, list[tuple[str, str]]] = {
    "Operations & live": [
        ("System overview", "1. System Overview"),
        ("Live trading", "2. Live Trading"),
        ("Market data", "6. Market Data"),
        ("Journal", "10. Journal"),
    ],
    "Risk & funded": [
        ("Risk center", "3. Risk Center"),
        ("Funded rules", "4. Funded Rules"),
    ],
    "Signals & regime": [
        ("Regime monitor", "5. Regime Monitor"),
        ("Strategy builder", "16. Strategy Builder"),
    ],
    "Research": [
        ("Backtesting", "7. Backtesting"),
        ("Walk forward", "8. Walk Forward"),
        ("Monte Carlo", "9. Monte Carlo"),
    ],
    "Execution": [
        ("Execution analytics", "11. Execution Analytics"),
    ],
    "Diagnostics": [
        ("Event bus", "12. Event Bus Monitor"),
        ("Recovery", "13. Recovery Dashboard"),
        ("Logs", "14. Logs Viewer"),
        ("Alerts", "18. Alerts Center"),
    ],
    "Platform & VPS": [
        ("Config editor", "15. Config Editor"),
        ("VPS health", "17. VPS Health"),
    ],
}

# One-line setup: deps + whether code changes are usually needed.
PAGE_SETUP_HINT: dict[str, str] = {
    "1. System Overview": (
        "**Runs:** Streamlit only reads DB/Redis; live subscriber status needs **`python run.py`** + Redis. "
        "**Setup:** `.env`, Redis, optional MT5 + `MT5_SYMBOLS`. No code changes for normal use."
    ),
    "2. Live Trading": (
        "**Runs:** State + repos from SQLite; signals from DB. "
        "**Setup:** Engine optional for live updates; funded safety still tied to `run.py` + Redis for some paths."
    ),
    "3. Risk Center": (
        "**Runs:** Reads `state_store` + kill-switch module stats. "
        "**Setup:** SQLite journal path; Telegram optional (`.env`). Change YAML/risk constants only if tuning limits."
    ),
    "4. Funded Rules": (
        "**Runs:** Displays prop-style limits from config/state. "
        "**Setup:** Adjust `.env` / `config/*.yaml` for firm limits—no rebuild required."
    ),
    "5. Regime Monitor": (
        "**Runs:** Best after hub has candles (live feed or CSV import). "
        "**Setup:** DuckDB + candles; engine populates regime history when `run.py` + data flow active."
    ),
    "6. Market Data": (
        "**Runs:** Hub/sanitizer stats; CSV import works **offline**. Live ticks need **`python run.py`**, Redis, MT5, `MT5_SYMBOLS`. "
        "**Setup:** `pip install duckdb` for persistence; no code edits for feed beyond `.env`."
    ),
    "7. Backtesting": (
        "**Runs:** Uses DuckDB candles + strategy code. "
        "**Setup:** Import or generate history first; edit strategies under `strategies/` only when changing logic."
    ),
    "8. Walk Forward": (
        "**Runs:** Research pipeline over stored data. "
        "**Setup:** Same data prerequisites as backtests; parameters live in config/YAML or page controls."
    ),
    "9. Monte Carlo": (
        "**Runs:** Simulation over equity/trade samples. "
        "**Setup:** Needs prior backtest or exported stats; no MT5 required."
    ),
    "10. Journal": (
        "**Runs:** SQLite trade rows via repositories. "
        "**Setup:** Engine fills journal when execution path writes trades; or inspect historical DB only."
    ),
    "11. Execution Analytics": (
        "**Runs:** Profiler / fill metrics when fills exist in storage or Redis snapshots. "
        "**Setup:** DEMO MT5 + `run.py` for fresh fills; otherwise historical data only."
    ),
    "12. Event Bus Monitor": (
        "**Runs:** Diagnostics for **this** Python process. Live subscribers run inside **`run.py`**—counts here stay low in Streamlit-only sessions. "
        "**Setup:** None; open engine terminal for real throughput."
    ),
    "13. Recovery Dashboard": (
        "**Runs:** Reads recovery artifacts / DB state from last engine boot. "
        "**Setup:** Always review after crash; no code change unless extending recovery itself."
    ),
    "14. Logs Viewer": (
        "**Runs:** Reads JSON/text logs under engine `logs/`. "
        "**Setup:** `QUANTA_LOG_DIR` optional; ensure engine writes logs."
    ),
    "15. Config Editor": (
        "**Runs:** Edits YAML on disk + reload hook. "
        "**Setup:** File permissions on VPS; invalid YAML can break engine—validate before save."
    ),
    "16. Strategy Builder": (
        "**Runs:** Lists/enables strategies from config. "
        "**Setup:** Strategy code lives in `strategies/`; changing behaviour requires Python edits + restart engine."
    ),
    "17. VPS Health": (
        "**Runs:** OS metrics (disk/CPU/RAM) for this host. "
        "**Setup:** Optional `psutil`; no broker keys."
    ),
    "18. Alerts Center": (
        "**Runs:** Aggregates recent alerts from memory/log-backed sources. "
        "**Setup:** Depends on subsystems publishing alerts when engine runs."
    ),
}


def all_page_ids_in_order() -> list[str]:
    """Flatten PAGE_GROUPS preserving section order (for smoke tests / CI)."""
    out: list[str] = []
    for _sec, pairs in PAGE_GROUPS.items():
        for _label, pid in pairs:
            out.append(pid)
    return out
