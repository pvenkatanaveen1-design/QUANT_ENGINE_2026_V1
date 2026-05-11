"""Spread / cost protection — reads `market:*` from Redis, publishes `cost:*` (no orders)."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from core.bus import get_value, set_value
from core.config import load_config
from core.logger import get_logger

log = get_logger()

COST_GUARD_INTERVAL_SECONDS = 5

# Symbols monitored for funded-style cost gates (must match router expectations later).
SUPPORTED_SYMBOLS: tuple[str, ...] = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "XAUUSD",
    "BTCUSD",
)


def _parse_number(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def pip_size(symbol: str) -> float:
    """
    Price distance that counts as *one pip* for this project (beginner reference).

    - Majors EUR/GBP: 0.0001
    - JPY quote: 0.01
    - Gold XAU: 0.1 (USD)
    - BTC: 1.0 (USD)
    """
    u = str(symbol).upper()
    if u.startswith("XAU") or u.startswith("XAG"):
        return 0.1
    if u.startswith("BTC") or u.startswith("ETH"):
        return 1.0
    if "JPY" in u:
        return 0.01
    return 0.0001


def max_spread_pips_for_symbol(symbol: str, cfg: dict) -> float:
    """Threshold from `load_config()` / `.env` with safe defaults."""
    u = str(symbol).upper()
    if u.startswith("BTC"):
        return float(cfg.get("COST_MAX_SPREAD_PIPS_BTC", 500.0))
    if u.startswith("XAU"):
        return float(cfg.get("COST_MAX_SPREAD_PIPS_XAU", 30.0))
    if "JPY" in u:
        return float(cfg.get("COST_MAX_SPREAD_PIPS_JPY", 2.0))
    return float(cfg.get("COST_MAX_SPREAD_PIPS_MAJOR", 2.0))


def calculate_spread_pips(symbol: str, bid: float | None, ask: float | None) -> float | None:
    """Convert bid/ask difference into *pips* using `pip_size(symbol)`."""
    if bid is None or ask is None:
        return None
    b = float(bid)
    a = float(ask)
    diff = abs(a - b)
    pip = pip_size(symbol)
    if pip <= 0:
        return None
    return diff / pip


def is_spread_acceptable(symbol: str, spread_pips: float | None, cfg: dict) -> bool:
    """True when spread is at or below the configured ceiling."""
    if spread_pips is None:
        return False
    return float(spread_pips) <= max_spread_pips_for_symbol(symbol, cfg)


def evaluate_cost_state(symbol: str, cfg: dict) -> dict:
    """
    Read Redis market snapshot for one symbol and classify SAFE / WARNING / BLOCKED.

    Execution code should treat `BLOCKED` + `block_trading=True` as hard no-trade for costs.
    """
    sym = str(symbol).upper()
    bid = _parse_number(get_value(f"market:{sym}:bid"))
    ask = _parse_number(get_value(f"market:{sym}:ask"))
    spread_raw = _parse_number(get_value(f"market:{sym}:spread"))

    if bid is None or ask is None:
        return {
            "symbol": sym,
            "spread": spread_raw,
            "spread_pips": None,
            "status": "WARNING",
            "block_trading": False,
            "reason": "Missing market bid/ask - pulse may be offline or symbol not in MT5_SYMBOLS",
        }

    spread_price = abs(float(ask) - float(bid))
    # Prefer derived price spread; keeps us aligned with displayed bid/ask.
    spread_pips = calculate_spread_pips(sym, bid, ask)
    if spread_pips is None:
        return {
            "symbol": sym,
            "spread": spread_price,
            "spread_pips": None,
            "status": "WARNING",
            "block_trading": False,
            "reason": "Could not convert spread to pips (check pip_size rules)",
        }

    limit = max_spread_pips_for_symbol(sym, cfg)
    if not is_spread_acceptable(sym, spread_pips, cfg):
        return {
            "symbol": sym,
            "spread": spread_price,
            "spread_pips": round(float(spread_pips), 4),
            "status": "BLOCKED",
            "block_trading": True,
            "reason": f"Spread {spread_pips:.2f} pips exceeds limit {limit} pips for {sym}",
        }

    return {
        "symbol": sym,
        "spread": spread_price,
        "spread_pips": round(float(spread_pips), 4),
        "status": "SAFE",
        "block_trading": False,
        "reason": f"Within limit ({limit} pips max for {sym})",
    }


def publish_cost_status(results_by_symbol: dict[str, dict]) -> None:
    """Write per-symbol `cost:{symbol}:*` plus global `cost:last_update`."""
    now_ts = datetime.now(timezone.utc).timestamp()

    for sym in SUPPORTED_SYMBOLS:
        row = results_by_symbol.get(sym)
        if not row:
            row = {
                "symbol": sym,
                "spread": None,
                "spread_pips": None,
                "status": "WARNING",
                "block_trading": False,
                "reason": "No evaluation row produced",
            }
        prefix = f"cost:{sym}"
        try:
            set_value(f"{prefix}:spread", row.get("spread"))
            set_value(f"{prefix}:spread_pips", row.get("spread_pips"))
            set_value(f"{prefix}:status", row.get("status"))
            set_value(f"{prefix}:block_trading", bool(row.get("block_trading")))
            set_value(f"{prefix}:reason", row.get("reason"))
        except Exception as exc:  # noqa: BLE001
            log.warning(f"cost_guard | Redis write failed symbol={sym} err={exc}")

    try:
        set_value("cost:last_update", now_ts)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"cost_guard | cost:last_update failed err={exc}")

    blocked_syms = [s for s, r in results_by_symbol.items() if r.get("status") == "BLOCKED"]
    blocked_label = ", ".join(blocked_syms) if blocked_syms else "none"
    log.info(f"cost_guard | publish | symbols={len(results_by_symbol)} blocked={blocked_label}")


def evaluate_cost_guard_once(cfg: dict | None = None) -> dict[str, dict]:
    """Evaluate all supported symbols and publish to Redis."""
    if cfg is None:
        cfg = load_config()
    results = {}
    for sym in SUPPORTED_SYMBOLS:
        results[sym] = evaluate_cost_state(sym, cfg)
    publish_cost_status(results)
    return results


def run_cost_guard() -> None:
    """Daemon loop (~5 s): refresh spread gates from live `market:*` keys."""
    log.info(
        f"cost_guard | daemon start | interval_s={COST_GUARD_INTERVAL_SECONDS} | "
        f"symbols={list(SUPPORTED_SYMBOLS)}"
    )
    while True:
        try:
            evaluate_cost_guard_once()
        except Exception:  # noqa: BLE001
            log.exception("cost_guard | tick failed")
        time.sleep(COST_GUARD_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_cost_guard()
