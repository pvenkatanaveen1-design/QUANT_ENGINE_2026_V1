"""
MT5 broker bridge — demo market orders only, gated by router + safety Redis keys.

Phase 12 rules:
- SYSTEM_MODE must be TEST (`core.config.load_config`).
- `router:last_decision` must be APPROVED and must match the requested symbol.
- Redis shields: heartbeat HEALTHY, risk not blocking, kill switch not active.

This module is an adapter: it does not run a strategy loop. Call `send_market_order`
from tests or future OMS code after the router has approved the same symbol.
"""

from __future__ import annotations

import time
from typing import Any

from core.bus import get_value, set_value
from core.config import load_config
from core.logger import get_logger

log = get_logger()

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - optional until MT5 package installed
    mt5 = None  # type: ignore[assignment]

# Prop firms / this phase: never exceed micro size here.
ALLOWED_VOLUME = 0.01
ORDER_MAGIC = 2026
ORDER_COMMENT = "QUANT_ENGINE_2026"
DEVIATION_POINTS = 20


def _truthy(raw: object) -> bool:
    if raw is True:
        return True
    if raw is False or raw is None:
        return False
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw) != 0.0
    s = str(raw).strip().upper()
    return s in {"TRUE", "1", "YES", "Y", "ON", "ACTIVE"}


def initialize_mt5() -> bool:
    """
    Ensure the MT5 Python terminal API is ready.

    If `core.pulse` already initialized MT5 in this process, we reuse that session
    instead of fighting a second `initialize()` (MetaTrader5 is single-session).
    """
    if mt5 is None:
        log.error("broker_bridge | MetaTrader5 package not installed")
        return False

    try:
        if mt5.terminal_info() is not None:
            log.info("broker_bridge | MT5 session already active — reusing")
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("broker_bridge | terminal_info pre-check | {}", exc)

    # Delegate to pulse's well-tested login path (.env MT5_LOGIN / password / server).
    from core.pulse import initialize_mt5 as pulse_initialize_mt5  # local import

    return bool(pulse_initialize_mt5())


def _ensure_demo_account() -> tuple[bool, str]:
    """Refuse LIVE / funded accounts — Phase 12 is demo-only infrastructure."""
    if mt5 is None:
        return False, "MetaTrader5 not available"
    acc = mt5.account_info()
    if acc is None:
        return False, "account_info() returned None — is the terminal logged in?"
    demo_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
    if int(getattr(acc, "trade_mode", -1)) != int(demo_mode):
        return False, "Not a DEMO account — broker bridge blocked (Phase 12 safety)"
    return True, ""


def validate_execution_environment(symbol: str) -> tuple[bool, str]:
    """
    Pre-flight checks that do not require MT5 (Redis + config only).

    Fails closed: unknown / missing keys block execution with a clear reason.
    """
    sym = str(symbol).upper()

    cfg = load_config()
    mode = str(cfg.get("SYSTEM_MODE") or "").strip().upper()
    if mode != "TEST":
        return False, f"SYSTEM_MODE must be TEST for broker bridge (got {mode})"

    decision = get_value("router:last_decision")
    if str(decision or "").strip().upper() != "APPROVED":
        return False, f"Router not APPROVED (router:last_decision={decision!r})"

    routed_sym = get_value("router:last_symbol")
    if routed_sym is None or str(routed_sym).strip().upper() != sym:
        return (
            False,
            f"Symbol mismatch: request={sym} vs router:last_symbol={routed_sym!r}",
        )

    block = get_value("risk:block_trading")
    if block is None:
        return False, "Missing risk:block_trading — refusing execution"
    if _truthy(block):
        return False, "risk:block_trading is true"

    ks = get_value("kill_switch:active")
    if ks is not None:
        if str(ks).strip().upper() == "ACTIVE" or _truthy(ks):
            return False, "kill_switch:active is on"

    hb = get_value("heartbeat:overall")
    if hb is None or str(hb).strip().upper() != "HEALTHY":
        return False, f"heartbeat:overall not HEALTHY (got {hb!r})"

    return True, ""


def build_order_request(
    symbol: str,
    side: str,
    volume: float,
    *,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
) -> dict[str, Any]:
    """
    Build an `order_send` request dict (BUY/SELL market — TRADE_ACTION_DEAL).

    Price is required by MT5: ask for buys, bid for sells (live tick).
    """
    if mt5 is None:
        raise RuntimeError("MetaTrader5 not installed")

    sym = str(symbol).upper()
    sd = str(side).strip().upper()
    if sd not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")

    vol = float(volume)
    if abs(vol - ALLOWED_VOLUME) > 1e-9:
        raise ValueError(f"Phase 12 allows volume={ALLOWED_VOLUME} only (got {vol})")

    if not mt5.symbol_select(sym, True):
        raise RuntimeError(f"symbol_select failed for {sym}")

    tick = mt5.symbol_info_tick(sym)
    if tick is None:
        raise RuntimeError(f"No tick for {sym}")

    ask = float(tick.ask)
    bid = float(tick.bid)
    if sd == "BUY":
        price = ask
        otype = mt5.ORDER_TYPE_BUY
    else:
        price = bid
        otype = mt5.ORDER_TYPE_SELL

    info = mt5.symbol_info(sym)
    filling = getattr(mt5, "ORDER_FILLING_RETURN", 2)
    if info is not None:
        fm = int(getattr(info, "filling_mode", 0) or 0)
        ioc = int(getattr(mt5, "ORDER_FILLING_IOC", 0) or 0)
        fok = int(getattr(mt5, "ORDER_FILLING_FOK", 0) or 0)
        ret = int(getattr(mt5, "ORDER_FILLING_RETURN", 0) or 0)
        if ioc and (fm & ioc):
            filling = ioc
        elif fok and (fm & fok):
            filling = fok
        elif ret and (fm & ret):
            filling = ret

    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": vol,
        "type": otype,
        "price": price,
        "sl": float(stop_loss),
        "tp": float(take_profit),
        "deviation": DEVIATION_POINTS,
        "magic": ORDER_MAGIC,
        "comment": ORDER_COMMENT,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }


def handle_execution_response(result: object | None) -> dict[str, Any]:
    """
    Normalize MetaTrader5 `OrderSendResult` into execution:* friendly fields.

    Returns keys: status (FILLED|REJECTED), ticket (int|None), reason (str)
    """
    if result is None:
        err = mt5.last_error() if mt5 else None
        return {
            "status": "REJECTED",
            "ticket": None,
            "reason": f"order_send returned None | MT5 last_error={err}",
        }

    ret = int(getattr(result, "retcode", -1))
    done = int(getattr(mt5, "TRADE_RETCODE_DONE", 10009)) if mt5 else 10009
    partial = int(getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)) if mt5 else 10010

    if ret in {done, partial}:
        order_id = int(getattr(result, "order", 0) or 0)
        deal_id = int(getattr(result, "deal", 0) or 0)
        ticket = order_id if order_id else deal_id
        comment = getattr(result, "comment", "") or ""
        return {
            "status": "FILLED",
            "ticket": ticket if ticket else None,
            "reason": str(comment or "MT5 retcode OK"),
        }

    comment = getattr(result, "comment", "") or ""
    return {
        "status": "REJECTED",
        "ticket": None,
        "reason": f"MT5 retcode={ret} | {comment}".strip(),
    }


def publish_execution_status(
    *,
    symbol: str | None,
    side: str | None,
    volume: float | None,
    status: str,
    ticket: int | None,
    reason: str,
    bridge_status: str | None = None,
) -> None:
    """Write execution:* snapshot + optional bridge status (IDLE/RUNNING/ERROR)."""
    now = time.time()
    try:
        set_value("execution:last_symbol", symbol)
        set_value("execution:last_side", side)
        set_value("execution:last_volume", volume)
        set_value("execution:last_status", str(status).strip().upper())
        set_value("execution:last_ticket", ticket)
        set_value("execution:last_reason", str(reason or ""))
        set_value("execution:last_update", now)
        if bridge_status is not None:
            set_value("execution:bridge_status", str(bridge_status))
        log.info(
            "broker_bridge | publish | symbol={} side={} status={} ticket={}",
            symbol,
            side,
            status,
            ticket,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("broker_bridge | publish_execution_status failed | {}", exc)
        try:
            set_value("execution:bridge_status", "ERROR")
        except Exception:
            pass


def send_market_order(
    symbol: str,
    side: str,
    volume: float = ALLOWED_VOLUME,
    *,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
) -> dict[str, Any]:
    """
    Full path: validate env → MT5 demo check → build → order_send → publish execution:*.

    Returns a dict including: ok (bool), status, ticket, reason, request (optional).
    """
    sym = str(symbol).upper()
    sd = str(side).strip().upper()

    ok_env, env_reason = validate_execution_environment(sym)
    if not ok_env:
        log.warning("broker_bridge | blocked | {}", env_reason)
        publish_execution_status(
            symbol=sym,
            side=sd,
            volume=float(volume),
            status="BLOCKED",
            ticket=None,
            reason=env_reason,
            bridge_status="RUNNING",
        )
        return {"ok": False, "status": "BLOCKED", "ticket": None, "reason": env_reason}

    if not initialize_mt5():
        msg = "MT5 initialize failed"
        publish_execution_status(
            symbol=sym,
            side=sd,
            volume=float(volume),
            status="REJECTED",
            ticket=None,
            reason=msg,
            bridge_status="ERROR",
        )
        return {"ok": False, "status": "REJECTED", "ticket": None, "reason": msg}

    demo_ok, demo_reason = _ensure_demo_account()
    if not demo_ok:
        log.error("broker_bridge | {}", demo_reason)
        publish_execution_status(
            symbol=sym,
            side=sd,
            volume=float(volume),
            status="REJECTED",
            ticket=None,
            reason=demo_reason,
            bridge_status="ERROR",
        )
        return {"ok": False, "status": "REJECTED", "ticket": None, "reason": demo_reason}

    try:
        req = build_order_request(sym, sd, volume, stop_loss=stop_loss, take_profit=take_profit)
    except Exception as exc:  # noqa: BLE001
        log.exception("broker_bridge | build_order_request failed")
        publish_execution_status(
            symbol=sym,
            side=sd,
            volume=float(volume),
            status="REJECTED",
            ticket=None,
            reason=f"build_order_request: {exc}",
            bridge_status="RUNNING",
        )
        return {"ok": False, "status": "REJECTED", "ticket": None, "reason": str(exc)}

    result = mt5.order_send(req) if mt5 else None
    handled = handle_execution_response(result)
    st = handled["status"]
    pub_st = "FILLED" if st == "FILLED" else "REJECTED"
    publish_execution_status(
        symbol=sym,
        side=sd,
        volume=float(volume),
        status=pub_st,
        ticket=handled.get("ticket"),
        reason=str(handled.get("reason") or ""),
        bridge_status="RUNNING",
    )
    ok = st == "FILLED"
    return {
        "ok": ok,
        "status": handled["status"],
        "ticket": handled.get("ticket"),
        "reason": handled.get("reason"),
        "request": req,
    }


def run_broker_bridge() -> None:
    """
    `run.py` worker: connect (or reuse) MT5, verify DEMO, then stay idle.

    There is **no auto-trading loop** — orders only happen via `send_market_order`
    or test harnesses.
    """
    log.info("broker_bridge | worker | Phase 12 | idle (no automatic order loop)")
    if not initialize_mt5():
        raise RuntimeError("broker_bridge | MT5 initialize failed — check pulse/terminal")

    demo_ok, demo_reason = _ensure_demo_account()
    if not demo_ok:
        raise RuntimeError(demo_reason)

    publish_execution_status(
        symbol=None,
        side=None,
        volume=None,
        status="IDLE",
        ticket=None,
        reason="Bridge online — waiting for manual/test orders",
        bridge_status="IDLE",
    )

    while True:
        time.sleep(60.0)


def run_broker_bridge_test() -> dict[str, Any]:
    """
    Minimal probe: EURUSD BUY 0.01 after *you* seeded router:* as APPROVED.

    Prefer `python scripts/test_broker_bridge.py` for full scenarios.
    """
    log.info("broker_bridge | run_broker_bridge_test | EURUSD BUY 0.01")
    return send_market_order("EURUSD", "BUY", ALLOWED_VOLUME)


if __name__ == "__main__":
    run_broker_bridge()
