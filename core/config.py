"""Simple configuration loader for foundation phase."""

# We import os to read values from environment variables.
import os

# We import load_dotenv so Python can read values from the `.env` file automatically.
from dotenv import load_dotenv

# We load `.env` immediately when this file is imported.
# This keeps setup beginner friendly because callers do not need to call load_dotenv manually.
load_dotenv()

# Mode logic lives in `core.system_mode` so Streamlit and other entry points avoid
# circular / partial imports against this larger module.
from core.system_mode import get_system_mode


def _float_env(name, default):
    """
    Read a float from the environment with a safe fallback.

    Empty or invalid strings fall back to `default` so the engine keeps running.
    """
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(str(raw).strip())
    except ValueError:
        return float(default)


def _str_env(name, default):
    """Read a stripped string; missing or blank uses `default`."""
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip()


# This function reads required foundation settings and returns them in a dictionary.
def load_config():
    # We read APP_NAME and provide a safe default if it is missing.
    app_name = os.getenv("APP_NAME", "QUANT_ENGINE_2026")

    # We read REDIS_HOST and default to localhost for local development.
    redis_host = os.getenv("REDIS_HOST", "localhost")

    # We read REDIS_PORT and convert it to integer for Redis client compatibility.
    redis_port = int(os.getenv("REDIS_PORT", "6379"))

    # ---------------------------
    # MetaTrader 5 (Phase 2)
    # ---------------------------
    mt5_login_raw = os.getenv("MT5_LOGIN", "").strip()
    mt5_login = int(mt5_login_raw) if mt5_login_raw else None

    mt5_password = os.getenv("MT5_PASSWORD", "")
    mt5_server = os.getenv("MT5_SERVER", "")

    mt5_symbols_raw = os.getenv("MT5_SYMBOLS", "")
    mt5_symbols = [s.strip() for s in mt5_symbols_raw.split(",") if s.strip()]

    # ---------------------------
    # Risk shield / funded rules (Phase 4.1)
    # ---------------------------
    # ACCOUNT_SIZE is a reference for sizing UI and future position math (shield uses live Redis equity).
    account_size = _float_env("ACCOUNT_SIZE", 100_000.0)

    # Drawdown gates are percentages (e.g. 3 means 3%).
    daily_dd_warning = _float_env("DAILY_DD_WARNING", 3.0)
    daily_dd_block = _float_env("DAILY_DD_BLOCK", 5.0)
    max_dd_block = _float_env("MAX_DD_BLOCK", 10.0)

    # Soft band before max DD block: optional MAX_DD_APPROACH, else max(0, max_dd_block - 2).
    max_dd_approach_raw = os.getenv("MAX_DD_APPROACH", "").strip()
    if max_dd_approach_raw:
        max_dd_approach = _float_env("MAX_DD_APPROACH", max(0.0, max_dd_block - 2.0))
    else:
        max_dd_approach = max(0.0, max_dd_block - 2.0)

    default_risk_per_trade = _float_env("DEFAULT_RISK_PER_TRADE", 0.5)
    prop_firm_name = _str_env("PROP_FIRM_NAME", "Unknown")

    # ---------------------------
    # Cost guard / spread limits (Phase 9) — max spread in *pips* (see risk/cost_guard.py).
    # ---------------------------
    cost_max_spread_pips_major = _float_env("COST_MAX_SPREAD_PIPS_MAJOR", 2.0)
    cost_max_spread_pips_jpy = _float_env("COST_MAX_SPREAD_PIPS_JPY", 2.0)
    cost_max_spread_pips_xau = _float_env("COST_MAX_SPREAD_PIPS_XAU", 30.0)
    cost_max_spread_pips_btc = _float_env("COST_MAX_SPREAD_PIPS_BTC", 500.0)

    # Trade manager simulation (Phase 15) — 1R size in account currency for demo logic.
    trade_manager_r_unit = _float_env("TRADE_MANAGER_R_UNIT", 10.0)

    # ATR feature engine (Phase 16) — M5 ATR vs price thresholds (tune per broker/symbol scale).
    atr_vol_low_major = _float_env("ATR_VOL_LOW_MAJOR", 0.00035)
    atr_vol_high_major = _float_env("ATR_VOL_HIGH_MAJOR", 0.00075)
    atr_vol_low_jpy = _float_env("ATR_VOL_LOW_JPY", 0.045)
    atr_vol_high_jpy = _float_env("ATR_VOL_HIGH_JPY", 0.110)
    atr_vol_low_xau = _float_env("ATR_VOL_LOW_XAU", 1.0)
    atr_vol_high_xau = _float_env("ATR_VOL_HIGH_XAU", 2.5)
    atr_vol_low_btc = _float_env("ATR_VOL_LOW_BTC", 35.0)
    atr_vol_high_btc = _float_env("ATR_VOL_HIGH_BTC", 110.0)

    # TEST vs LIVE drives dashboards and safety banners (future: routing live Redis vs demo).
    system_mode = get_system_mode()

    return {
        "APP_NAME": app_name,
        "REDIS_HOST": redis_host,
        "REDIS_PORT": redis_port,
        "MT5_LOGIN": mt5_login,
        "MT5_PASSWORD": mt5_password,
        "MT5_SERVER": mt5_server,
        "MT5_SYMBOLS": mt5_symbols,
        # Risk / funded configuration (single source of truth for limits).
        "ACCOUNT_SIZE": account_size,
        "DAILY_DD_WARNING": daily_dd_warning,
        "DAILY_DD_BLOCK": daily_dd_block,
        "MAX_DD_BLOCK": max_dd_block,
        "MAX_DD_APPROACH": max_dd_approach,
        "DEFAULT_RISK_PER_TRADE": default_risk_per_trade,
        "PROP_FIRM_NAME": prop_firm_name,
        "COST_MAX_SPREAD_PIPS_MAJOR": cost_max_spread_pips_major,
        "COST_MAX_SPREAD_PIPS_JPY": cost_max_spread_pips_jpy,
        "COST_MAX_SPREAD_PIPS_XAU": cost_max_spread_pips_xau,
        "COST_MAX_SPREAD_PIPS_BTC": cost_max_spread_pips_btc,
        "TRADE_MANAGER_R_UNIT": trade_manager_r_unit,
        "ATR_VOL_LOW_MAJOR": atr_vol_low_major,
        "ATR_VOL_HIGH_MAJOR": atr_vol_high_major,
        "ATR_VOL_LOW_JPY": atr_vol_low_jpy,
        "ATR_VOL_HIGH_JPY": atr_vol_high_jpy,
        "ATR_VOL_LOW_XAU": atr_vol_low_xau,
        "ATR_VOL_HIGH_XAU": atr_vol_high_xau,
        "ATR_VOL_LOW_BTC": atr_vol_low_btc,
        "ATR_VOL_HIGH_BTC": atr_vol_high_btc,
        "SYSTEM_MODE": system_mode,
    }


def _validate_config_dict(cfg: dict) -> dict:
    """
    Ensure types are safe for Redis/UI consumers.

    Normalizes the provided dict in place (port, symbol list, SYSTEM_MODE) and returns it.
    """
    port = cfg.get("REDIS_PORT", 6379)
    try:
        cfg["REDIS_PORT"] = int(port)
    except (TypeError, ValueError):
        cfg["REDIS_PORT"] = 6379

    symbols = cfg.get("MT5_SYMBOLS")
    if not isinstance(symbols, list):
        cfg["MT5_SYMBOLS"] = []
    else:
        cfg["MT5_SYMBOLS"] = [str(s).strip() for s in symbols if str(s).strip()]

    mode = str(cfg.get("SYSTEM_MODE") or "TEST").strip().upper()
    cfg["SYSTEM_MODE"] = mode if mode in {"TEST", "LIVE"} else "TEST"

    try:
        cfg["TRADE_MANAGER_R_UNIT"] = max(0.01, float(cfg.get("TRADE_MANAGER_R_UNIT", 10.0)))
    except (TypeError, ValueError):
        cfg["TRADE_MANAGER_R_UNIT"] = 10.0

    def _band(low_key: str, high_key: str, d_low: float, d_high: float) -> None:
        try:
            lo = float(cfg.get(low_key, d_low))
            hi = float(cfg.get(high_key, d_high))
        except (TypeError, ValueError):
            lo, hi = d_low, d_high
        if lo <= 0 or hi <= 0 or lo >= hi:
            lo, hi = d_low, d_high
        cfg[low_key] = lo
        cfg[high_key] = hi

    _band("ATR_VOL_LOW_MAJOR", "ATR_VOL_HIGH_MAJOR", 0.00035, 0.00075)
    _band("ATR_VOL_LOW_JPY", "ATR_VOL_HIGH_JPY", 0.045, 0.110)
    _band("ATR_VOL_LOW_XAU", "ATR_VOL_HIGH_XAU", 1.0, 2.5)
    _band("ATR_VOL_LOW_BTC", "ATR_VOL_HIGH_BTC", 35.0, 110.0)

    return cfg


def get_all_config():
    """
    Full validated settings dict (same keys as load_config).

    Defined entirely in this module so `from core.config import get_all_config` always works
    (no dependency on other core modules at definition time).
    """
    return _validate_config_dict(dict(load_config()))


def describe_mt5_feed_readiness():
    """
    Summarize whether MT5 tick ingestion can succeed — safe from dashboard/run.py.

    Demo/live prices come from the logged-in MT5 terminal session; there is no MT5_DEMO env flag.
    """
    cfg = load_config()
    symbols = list(cfg.get("MT5_SYMBOLS") or [])
    issues = []
    hints = []

    if not symbols:
        issues.append(
            "MT5_SYMBOLS is empty — pulse exits immediately (pulse:status=no_symbols_configured)."
        )
        hints.append("Fix: set comma-separated broker symbols in `.env`, e.g. MT5_SYMBOLS=XAUUSD,EURUSD")

    broker_hints = []
    if not (cfg.get("MT5_SERVER") or "").strip():
        broker_hints.append(
            "MT5_SERVER is blank — initialize may still work if MetaTrader is already logged in."
        )
    if cfg.get("MT5_LOGIN") is None:
        broker_hints.append(
            "MT5_LOGIN is blank — pulse attempts mt5.initialize() without explicit credentials."
        )

    strict_raw = os.getenv("QUANT_STRICT_MT5_CONFIG", "").strip().lower()
    strict = strict_raw in ("1", "true", "yes", "on")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "hints": hints + broker_hints,
        "symbols": symbols,
        "system_mode": str(cfg.get("SYSTEM_MODE") or "TEST"),
        "strict_exit_requested": strict,
    }


# Explicit public API for `from core.config import *` and static checkers.
__all__ = ["load_config", "get_all_config", "describe_mt5_feed_readiness"]
