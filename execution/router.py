"""
Execution router — single gate before any trade is sent to a broker.

This module only reads/writes Redis. It does not place orders or talk to MT5.
Future strategy and OMS code should call `evaluate_trade_request(symbol)` and honor
the decision (APPROVED / BLOCKED) before doing anything else.
"""

from __future__ import annotations

import time
from typing import Callable

from core.bus import get_value, set_value
from core.config import load_config
from core.logger import get_logger

log = get_logger()

# Only these decisions are written to Redis (keeps downstream parsers simple).
DECISION_APPROVED = "APPROVED"
DECISION_BLOCKED = "BLOCKED"

# Labels published by `core/clock.py` where we allow *new* discretionary entries.
# Weekend / off-hours are blocked to reduce gap risk and thin books (beginner-safe default).
_TRADEABLE_SESSIONS: frozenset[str] = frozenset(
    {
        "Asia",
        "London",
        "New York",
        "London-New York overlap",
    }
)


def _truthy(raw: object) -> bool:
    """Coerce Redis/JSON values into a strict boolean *true*."""
    if raw is True:
        return True
    if raw is False or raw is None:
        return False
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw) != 0.0
    s = str(raw).strip().upper()
    return s in {"TRUE", "1", "YES", "Y", "ON", "ACTIVE"}


def check_heartbeat() -> tuple[bool, str]:
    """
    Heartbeat rollup must be HEALTHY. Anything else (or a missing key) is treated as
    *missing / unhealthy system* — we refuse to approve trades.
    """
    try:
        overall = get_value("heartbeat:overall")
    except Exception as exc:  # noqa: BLE001
        log.warning("router | check_heartbeat | redis_err={}", exc)
        return False, f"Heartbeat check failed ({exc})"

    if overall is None:
        return False, "Missing system health: heartbeat:overall not set"

    if str(overall).strip().upper() != "HEALTHY":
        return False, f"Heartbeat unhealthy (heartbeat:overall={overall})"

    return True, ""


def check_shield() -> tuple[bool, str]:
    """Risk shield: when `risk:block_trading` is true, all new trades stop."""
    try:
        block = get_value("risk:block_trading")
    except Exception as exc:  # noqa: BLE001
        log.warning("router | check_shield | redis_err={}", exc)
        return False, f"Shield check failed ({exc})"

    if block is None:
        return False, "Missing risk:block_trading (is risk/shield publishing?)"

    if _truthy(block):
        return False, "Risk shield is blocking trading"

    return True, ""


def check_kill_switch() -> tuple[bool, str]:
    """
    Operator kill switch. When `kill_switch:active` is true, trades are blocked.

    If the key is missing, we treat the switch as *off* so local dev works without
    seeding the key every time.
    """
    try:
        active = get_value("kill_switch:active")
    except Exception as exc:  # noqa: BLE001
        log.warning("router | check_kill_switch | redis_err={}", exc)
        return False, f"Kill switch check failed ({exc})"

    if active is None:
        return True, ""

    if _truthy(active):
        return False, "Kill switch active"

    return True, ""


def check_news_guard() -> tuple[bool, str]:
    """News simulation / calendar: ACTIVE blackout means no new risk."""
    try:
        blackout = get_value("news:blackout")
    except Exception as exc:  # noqa: BLE001
        log.warning("router | check_news_guard | redis_err={}", exc)
        return False, f"News guard check failed ({exc})"

    if blackout is None:
        return False, "Missing news:blackout (run news guard once)"

    if str(blackout).strip().upper() == "ACTIVE":
        detail = get_value("news:blackout_reason")
        suffix = f" - {detail}" if detail else ""
        return False, f"News blackout active{suffix}"

    return True, ""


def check_cost_guard(symbol: str) -> tuple[bool, str]:
    """Per-symbol spread gate from Phase 9 (`risk/cost_guard.py`)."""
    sym = str(symbol).upper()
    key = f"cost:{sym}:block_trading"
    try:
        flag = get_value(key)
    except Exception as exc:  # noqa: BLE001
        log.warning("router | check_cost_guard | redis_err={}", exc)
        return False, f"Cost guard check failed ({exc})"

    if flag is None:
        return False, f"Missing {key} (run cost guard or tests to populate cost:*)"

    if _truthy(flag):
        rsn = get_value(f"cost:{sym}:reason")
        extra = f": {rsn}" if rsn else ""
        return False, f"Cost guard blocking {sym}{extra}"

    return True, ""


def check_session() -> tuple[bool, str]:
    """Session must be a known liquid window (see `_TRADEABLE_SESSIONS`)."""
    try:
        session = get_value("clock:session")
    except Exception as exc:  # noqa: BLE001
        log.warning("router | check_session | redis_err={}", exc)
        return False, f"Session check failed ({exc})"

    if session is None or str(session).strip() == "":
        return False, "Invalid session: clock:session missing"

    label = str(session).strip()
    if label not in _TRADEABLE_SESSIONS:
        return False, f"Invalid session for trading: {label}"

    return True, ""


def _check_system_mode_redis_or_config() -> tuple[bool, str]:
    """
    SYSTEM_MODE must be TEST or LIVE.

    Reads Redis key `SYSTEM_MODE` first (so ops can mirror config into Redis),
    then falls back to `core.config.load_config()` (your `.env` snapshot).
    """
    mode: str | None = None
    try:
        raw = get_value("SYSTEM_MODE")
    except Exception as exc:  # noqa: BLE001
        log.warning("router | system_mode | redis_err={}", exc)
        return False, f"SYSTEM_MODE read failed ({exc})"

    if raw is not None:
        mode = str(raw).strip().upper()
    else:
        try:
            mode = str(load_config().get("SYSTEM_MODE") or "").strip().upper()
        except Exception as exc:  # noqa: BLE001
            return False, f"SYSTEM_MODE missing in Redis and config ({exc})"

    if mode not in {"TEST", "LIVE"}:
        return False, f"Invalid SYSTEM_MODE (expected TEST or LIVE): {mode!r}"

    return True, ""


def publish_router_decision(
    symbol: str,
    decision: str,
    reason: str,
    *,
    router_status: str = "READY",
) -> None:
    """
    Persist the latest gate result for dashboards and external tools.

    Keys:
      router:last_symbol, router:last_decision, router:last_reason,
      router:last_update (unix seconds), router:status
    """
    sym = str(symbol).upper()
    dec = str(decision).strip().upper()
    if dec not in {DECISION_APPROVED, DECISION_BLOCKED}:
        dec = DECISION_BLOCKED

    msg = str(reason or "").strip()
    if not msg:
        msg = "Blocked" if dec == DECISION_BLOCKED else "All checks passed"

    now = time.time()
    st = str(router_status or "READY").strip() or "READY"

    try:
        set_value("router:last_symbol", sym)
        set_value("router:last_decision", dec)
        set_value("router:last_reason", msg)
        set_value("router:last_update", now)
        set_value("router:status", st)
        log.info("router | publish | symbol={} decision={} status={}", sym, dec, st)
    except Exception as exc:  # noqa: BLE001
        log.error("router | publish failed | {}", exc)
        try:
            set_value("router:status", "ERROR")
        except Exception:
            pass


def evaluate_trade_request(symbol: str) -> dict:
    """
    Run every safety check in order; first failure produces BLOCKED.

    Returns a dict with: symbol, decision (APPROVED|BLOCKED), reason, failed_check.
    """
    sym = str(symbol).upper()

    # Order matches the product requirement list (heartbeat → … → session),
    # then SYSTEM_MODE as the final sanity gate.
    checks: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("heartbeat", check_heartbeat),
        ("shield", check_shield),
        ("kill_switch", check_kill_switch),
        ("news", check_news_guard),
        ("cost", lambda: check_cost_guard(sym)),
        ("session", check_session),
        ("system_mode", _check_system_mode_redis_or_config),
    ]

    for name, fn in checks:
        ok, reason = fn()
        if not ok:
            log.info("router | evaluate | {} | FAIL | {} | {}", sym, name, reason)
            publish_router_decision(sym, DECISION_BLOCKED, reason)
            return {
                "symbol": sym,
                "decision": DECISION_BLOCKED,
                "reason": reason,
                "failed_check": name,
            }

    ok_reason = "All safety checks passed"
    log.info("router | evaluate | {} | APPROVED", sym)
    publish_router_decision(sym, DECISION_APPROVED, ok_reason)
    return {
        "symbol": sym,
        "decision": DECISION_APPROVED,
        "reason": ok_reason,
        "failed_check": None,
    }


def run_router_test() -> None:
    """
    Quick smoke helper: evaluates a few symbols against *current* Redis snapshots.

    Intended for `python -m execution.router` — real demos live in `scripts/test_router.py`.
    """
    log.info("router | run_router_test | start")
    for sym in ("EURUSD", "XAUUSD", "BTCUSD"):
        result = evaluate_trade_request(sym)
        log.info(
            "router | run_router_test | {} -> {} ({})",
            sym,
            result["decision"],
            result["reason"],
        )
    log.info("router | run_router_test | done")


if __name__ == "__main__":
    run_router_test()
