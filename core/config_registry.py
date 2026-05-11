"""Centralized runtime configuration visibility (Phase 7).

Reads validated settings from core.config and publishes them to Redis on a fixed interval
so dashboards (and future editable UIs) share one snapshot — no `.env` writes here.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from core.bus import set_value
from core.config_helpers import build_all_config
from core.logger import get_logger

# Shared logger name matches other core modules for easy grepping.
log = get_logger()

# How long to wait between Redis publishes (seconds).
PUBLISH_INTERVAL_SEC = 30


def collect_config() -> dict[str, Any]:
    """
    Gather every setting we expose for visibility (safe, JSON-serializable values only).

    Intentionally omits secrets such as MT5_PASSWORD — those never belong in Redis
    for a read-only ops dashboard.
    """
    cfg = build_all_config()

    symbols = cfg.get("MT5_SYMBOLS")
    if not isinstance(symbols, list):
        symbols = []

    # Floats/ints from get_all_config are already normalized; still use safe fallbacks.
    account_size = cfg.get("ACCOUNT_SIZE")
    try:
        account_size = float(account_size)
    except (TypeError, ValueError):
        account_size = 0.0

    def _pct(key: str, default: float) -> float:
        raw = cfg.get(key, default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    return {
        "config:system_mode": str(cfg.get("SYSTEM_MODE") or "TEST"),
        "config:symbols": symbols,
        "config:account_size": account_size,
        "config:daily_dd_warning": _pct("DAILY_DD_WARNING", 3.0),
        "config:daily_dd_block": _pct("DAILY_DD_BLOCK", 5.0),
        "config:max_dd_block": _pct("MAX_DD_BLOCK", 10.0),
        "config:default_risk_per_trade": _pct("DEFAULT_RISK_PER_TRADE", 0.5),
        "config:prop_firm": str(cfg.get("PROP_FIRM_NAME") or "Unknown"),
        "config:redis_host": str(cfg.get("REDIS_HOST") or "localhost"),
        "config:redis_port": int(cfg.get("REDIS_PORT") or 6379),
        "config:last_update": datetime.now(timezone.utc).isoformat(),
    }


def publish_config() -> None:
    """Write the current visibility snapshot to Redis (one SET per key)."""
    snapshot = collect_config()
    for redis_key, value in snapshot.items():
        try:
            set_value(redis_key, value)
        except Exception as exc:  # noqa: BLE001 — keep registry alive; log and continue.
            log.warning(f"config_registry | skipped key={redis_key} | reason={exc}")

    log.info(
        "config_registry | published | keys=%s | mode=%s",
        len(snapshot),
        snapshot.get("config:system_mode"),
    )


def run_config_registry() -> None:
    """
    Main loop for `run.py`: publish immediately, then every PUBLISH_INTERVAL_SEC.

    Runs forever until the process/thread stops — same pattern as pulse/clock workers.
    """
    log.info("config_registry | loop start | interval_s=%s", PUBLISH_INTERVAL_SEC)
    while True:
        try:
            publish_config()
        except Exception as exc:  # noqa: BLE001 — never let the thread die silently.
            log.exception("config_registry | publish_config failed | %s", exc)

        time.sleep(PUBLISH_INTERVAL_SEC)
