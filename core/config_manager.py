"""
core/config_manager.py — Central config loader for all YAML files + .env.

WHY THIS FILE EXISTS
--------------------
Each of the 45 systems needs configuration (risk limits, thresholds, symbols).
Without this, every system reads its own YAML file differently → chaos.
Here: one load() function, one cache, one reload() command.

Systems import like:
    from core import config
    limits = config.load("risk_rules")
    daily_dd = config.get("risk_rules", "max_daily_dd_pct")

2026 USAGE NOTES
----------------
- YAML files in config/ contain the REAL runtime values.
- constants.py contains hardcoded fallback defaults.
- .env contains SECRETS (MT5 login, passwords) — never put in YAML.
- config.reload() can be called from dashboard sliders without restart.

HOW TO ADD A NEW CONFIG FILE
-----------------------------
1. Create config/your_file.yaml
2. Add it to CONFIG_FILES dict below
3. Call config.load("your_file") anywhere in the engine
"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from core.exceptions import ConfigLoadError, MissingConfigKey

# ─── LOAD .env ON IMPORT ─────────────────────────────────────────────────────
# Secrets (MT5 login, Telegram token) come from .env — never from YAML.
load_dotenv()

# ─── CONFIG FILE REGISTRY ────────────────────────────────────────────────────
CONFIG_DIR = Path("config")

# Add new YAML files here.  Key = name callers use.  Value = file path.
CONFIG_FILES: dict[str, Path] = {
    "risk_rules":   CONFIG_DIR / "risk_rules.yaml",
    "funded_rules": CONFIG_DIR / "funded_rules.yaml",
    "strategies":   CONFIG_DIR / "strategies.yaml",
    "regimes":      CONFIG_DIR / "regimes.yaml",
    "sessions":     CONFIG_DIR / "sessions.yaml",
    "symbols":      CONFIG_DIR / "symbols.yaml",
}

# ─── CACHE ───────────────────────────────────────────────────────────────────
# Files are read only once per process (or after reload() call).
# This prevents repeated disk I/O on every tick.
_cache: dict[str, dict] = {}


def load(config_name: str) -> dict:
    """
    Load a YAML config file by name.
    Returns the full dict from the YAML file.
    Raises ConfigLoadError if file is missing or malformed.

    Example:
        risk = config.load("risk_rules")
        daily_dd = risk["max_daily_dd_pct"]    # 0.04
    """
    # Return cached version if already loaded
    if config_name in _cache:
        return _cache[config_name]

    # Validate the config name is known
    if config_name not in CONFIG_FILES:
        known = list(CONFIG_FILES.keys())
        raise ConfigLoadError(
            f"Unknown config name: '{config_name}'. "
            f"Known names: {known}"
        )

    path = CONFIG_FILES[config_name]

    # Check file exists (provide helpful error if not)
    if not path.exists():
        raise ConfigLoadError(
            f"Config file not found: {path}\n"
            f"Create it by copying from the examples in config/\n"
            f"Working directory: {Path.cwd()}"
        )

    # Parse YAML
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _cache[config_name] = data or {}
        return _cache[config_name]
    except yaml.YAMLError as exc:
        raise ConfigLoadError(
            f"Invalid YAML in {path}: {exc}"
        )


def get(config_name: str, key: str, default: Any = None) -> Any:
    """
    Get a specific key from a config file.
    Returns default if key is not found (and default is not None).
    Raises MissingConfigKey if key missing and no default given.

    Example:
        spread_limit = config.get("risk_rules", "max_spread_pips")      # 1.5
        daily_dd     = config.get("risk_rules", "max_daily_dd_pct", 0.04)  # 0.04
    """
    data = load(config_name)
    if key in data:
        return data[key]
    if default is not None:
        return default
    raise MissingConfigKey(
        f"Key '{key}' not found in config '{config_name}'. "
        f"Available keys: {list(data.keys())}"
    )


def get_nested(config_name: str, *keys: str, default: Any = None) -> Any:
    """
    Access nested YAML keys using dot-path style positional args.
    Example: config.get_nested("funded_rules", "FTMO", "daily_dd_pct")
    """
    data = load(config_name)
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            if default is not None:
                return default
            raise MissingConfigKey(
                f"Nested key path {keys} not found in '{config_name}'"
            )
        current = current[key]
    return current


def reload(config_name: str | None = None) -> None:
    """
    Clear cache and force re-read from disk.

    Call with no argument to reload ALL config files.
    Call with a name to reload just one file.

    Used by:
    - Dashboard sliders that write updated thresholds to YAML
    - Unit tests that need fresh state
    - Admin Telegram commands

    Example:
        config.reload()                  # reload everything
        config.reload("risk_rules")      # reload only risk_rules.yaml
    """
    global _cache
    if config_name:
        _cache.pop(config_name, None)
    else:
        _cache.clear()


# ─── ENVIRONMENT VARIABLE HELPERS ────────────────────────────────────────────

def env(key: str, default: str | None = None, required: bool = False) -> str | None:
    """
    Read an environment variable from .env file.
    Secrets ALWAYS come from .env — never hardcoded in YAML or Python.

    Parameters:
        key:      e.g. "MT5_LOGIN"
        default:  value to return if key is not set
        required: if True and key missing, raises MissingConfigKey

    Example:
        login = config.env("MT5_LOGIN", required=True)
        token = config.env("TELEGRAM_BOT_TOKEN", required=True)
        mode  = config.env("SYSTEM_MODE", default="TEST")
    """
    value = os.getenv(key, default)
    if required and value is None:
        raise MissingConfigKey(
            f"Required environment variable '{key}' not set in .env file.\n"
            f"Add: {key}=your_value to your .env file."
        )
    return value


# ─── CONVENIENCE FUNCTIONS ───────────────────────────────────────────────────

def mt5_credentials() -> dict:
    """
    Return MT5 login details from .env.
    Raises MissingConfigKey if any required field is missing.
    """
    return {
        "login":    int(env("MT5_LOGIN", required=True)),
        "password": env("MT5_PASSWORD", required=True),
        "server":   env("MT5_SERVER", required=True),
    }


def risk_limits() -> dict:
    """Return full risk_rules.yaml dict."""
    return load("risk_rules")


def funded_firm_rules() -> dict:
    """
    Return the active prop firm's rules from funded_rules.yaml.
    Reads 'active_firm' key to know which firm section to return.
    """
    data      = load("funded_rules")
    firm_name = data.get("active_firm", "CUSTOM")
    if firm_name not in data:
        raise ConfigLoadError(
            f"active_firm='{firm_name}' not found in funded_rules.yaml. "
            f"Available firms: {[k for k in data if k != 'active_firm']}"
        )
    return data[firm_name]


def active_strategies() -> list[str]:
    """Return list of enabled strategy names from strategies.yaml."""
    data       = load("strategies")
    candidates = data.get("active_strategies", [])
    # Only return strategies that also have enabled: true in their section
    return [
        name for name in candidates
        if data.get(name, {}).get("enabled", False)
    ]


def symbol_config(symbol: str) -> dict:
    """
    Return config dict for a specific symbol.
    Raises ConfigLoadError if symbol not found in symbols.yaml.
    """
    data = load("symbols")
    if symbol not in data:
        raise ConfigLoadError(
            f"Symbol '{symbol}' not found in config/symbols.yaml. "
            f"Add it before trading."
        )
    return data[symbol]
