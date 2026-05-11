"""
MT5 demo-only order execution. No Redis.

HARD BLOCK: non-demo accounts raise RuntimeError unless
``config/risk.yaml`` sets ``funded_account.enabled: true`` (still requires code review).
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _ROOT / "logs"
_TRADES_CSV = _LOG_DIR / "trades.csv"

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None  # type: ignore[assignment]


def get_account_type() -> str:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 not installed")
    a = mt5.account_info()
    if a is None:
        raise RuntimeError("account_info None")
    mode = int(getattr(a, "trade_mode", -1))
    demo = int(getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0))
    return "demo" if mode == demo else "live"


def execute_trade(signal: dict[str, Any], lot_size: float, account_info: dict[str, Any]) -> dict[str, Any]:
    """Send market order on demo. Returns result metadata."""
    try:
        if mt5 is None:
            raise RuntimeError("MT5 not available")

        risk_path = _ROOT / "config" / "risk.yaml"
        funded_ok = False
        try:
            import yaml

            if risk_path.exists():
                cfg = yaml.safe_load(risk_path.read_text(encoding="utf-8")) or {}
                funded_ok = bool(cfg.get("funded_account", {}).get("enabled", False))
        except Exception:
            pass

        if get_account_type() != "demo" and not funded_ok:
            raise RuntimeError("LIVE account blocked — enable funded_account.enabled only after manual code review.")

        sym = str(signal["symbol"])
        _ensure_sym(sym)

        direction = str(signal["direction"]).lower()
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            raise RuntimeError("no tick")

        price = float(tick.ask) if direction == "buy" else float(tick.bid)
        sl = float(signal["sl"])
        tp = float(signal["tp1"])
        vol = float(lot_size)
        comment = f"{signal.get('regime','')}-{signal.get('strategy','')}-{signal.get('score',0)}".replace(" ", "")[:31]

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": vol,
            "type": mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 25,
            "magic": 202607,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(req)
        if res is None:
            return {"ok": False, "error": "order_send returned None", "last_error": mt5.last_error()}
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            return {"ok": False, "retcode": res.retcode, "comment": res.comment}

        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": sym,
            "direction": direction,
            "strategy": signal.get("strategy"),
            "regime": signal.get("regime"),
            "score": signal.get("score"),
            "entry_price": price,
            "sl": sl,
            "tp1": tp,
            "lot_size": vol,
            "order_id": res.order,
            "result": "open",
            "exit_price": "",
            "pnl": "",
            "exit_reason": "",
        }
        _append_trade(row)
        return {"ok": True, "order": res.order, "deal": res.deal}
    except Exception as exc:
        log.exception("execute_trade: %s", exc)
        return {"ok": False, "error": str(exc)}


def _ensure_sym(sym: str) -> None:
    inf = mt5.symbol_info(sym)
    if inf is None:
        raise RuntimeError(f"unknown symbol {sym}")
    if not inf.visible:
        mt5.symbol_select(sym, True)


def _append_trade(row: dict[str, Any]) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = _TRADES_CSV
    header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if header:
            w.writeheader()
        w.writerow(row)


def log_trade(signal: dict[str, Any], lot_size: float, result: dict[str, Any]) -> None:
    try:
        log.info("trade logged | signal=%s | lot=%s | result=%s", signal.get("strategy"), lot_size, result)
    except Exception as exc:
        log.warning("log_trade: %s", exc)
