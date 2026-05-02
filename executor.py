from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.bus import RedisBus  # noqa: E402
from core.clock import utc_now  # noqa: E402
from core.constants import CHANNEL_SIGNALS_RAW, CHANNEL_TICKS_PATTERN  # noqa: E402
from core.tick_sanitizer import sanitize_tick_dict  # noqa: E402
from execution.broker_bridge import account_equity  # noqa: E402
from execution.router import ExecutionRouter  # noqa: E402
from logs.trade_journal import log_event  # noqa: E402
from qualifiers.correlation_filter import correlation_ok  # noqa: E402
from qualifiers.news_filter import passes_news_gate  # noqa: E402
from qualifiers.session_filter import passes_session  # noqa: E402
from qualifiers.spread_filter import spread_ok  # noqa: E402
from risk.equity_state import sync_baselines  # noqa: E402
from risk.kill_switch import halt as halt_trading  # noqa: E402
from risk.kill_switch import is_halted  # noqa: E402
from risk.position_sizer import load_pair_settings, volume_lots_for_risk  # noqa: E402
from risk.shield import Shield  # noqa: E402
from schemas.order import Order, OrderSide, OrderStatus  # noqa: E402
from schemas.signal import Signal  # noqa: E402
from schemas.tick import Tick  # noqa: E402

_LOG = logging.getLogger(__name__)
_LAST_TICK: dict[str, Tick] = {}
_LOCK = threading.Lock()


def _tick_listener() -> None:
    bus_ticks = RedisBus.from_env()
    for _, payload in bus_ticks.iter_pattern_messages(CHANNEL_TICKS_PATTERN):
        if not isinstance(payload, dict):
            continue
        cleaned = sanitize_tick_dict(dict(payload))
        tick = Tick.from_dict(cleaned)
        with _LOCK:
            _LAST_TICK[tick.symbol] = tick


def _build_order(signal: Signal, pair_cfg: dict, limits: dict) -> Order | None:
    with _LOCK:
        tick = _LAST_TICK.get(signal.symbol)
    if tick is None:
        _LOG.warning("no_tick_cached symbol=%s", signal.symbol)
        return None

    bid, ask = tick.bid, tick.ask
    session_ok = passes_session(tick.time_utc)
    spread_ok_flag, spread_val = spread_ok(signal.symbol, bid, ask, pair_cfg)
    news_ok_flag = passes_news_gate(signal.symbol)
    if not session_ok or not spread_ok_flag or not news_ok_flag:
        _LOG.debug(
            "filter_block sym=%s session=%s spread=%s news=%s",
            signal.symbol,
            session_ok,
            spread_ok_flag,
            news_ok_flag,
        )
        return None

    atr_estimate = float(
        signal.extras.get("atr_estimate", float(os.environ.get("QUANT_FALLBACK_ATR_PRICE", "0.00055")))
    )
    sl_mult = float(pair_cfg.get("atr_multiplier_sl", 1.5))
    tp_mult = float(pair_cfg.get("atr_multiplier_tp", 3.0))
    sl_dist_price = atr_estimate * sl_mult
    tp_dist_price = atr_estimate * tp_mult

    if signal.direction.lower() == "buy":
        side = OrderSide.BUY
        entry = float(ask)
        sl_price = entry - sl_dist_price
        tp_price = entry + tp_dist_price if tp_dist_price > 0 else None
    elif signal.direction.lower() == "sell":
        side = OrderSide.SELL
        entry = float(bid)
        sl_price = entry + sl_dist_price
        tp_price = entry - tp_dist_price if tp_dist_price > 0 else None
    else:
        return None

    equity_now = float(account_equity())
    risk_pct = float(limits.get("max_risk_per_trade_pct", 0.5))
    settings_blob = load_pair_settings()
    stop_px = abs(entry - sl_price)
    vol = volume_lots_for_risk(
        signal.symbol,
        equity_now,
        risk_pct,
        stop_distance_price=stop_px,
        settings=settings_blob,
    )

    magic = int(os.environ.get("QUANT_MAGIC", "880022"))
    log_event(
        "signal_preview",
        {
            "symbol": signal.symbol,
            "side": side.value,
            "risk_pct": risk_pct,
            "volume": vol,
            "spread_estimate": spread_val,
            "confluence_score": signal.confluence_score,
            "extras": dict(signal.extras),
        },
    )

    return Order(
        symbol=signal.symbol,
        side=side,
        volume_lots=float(vol),
        sl_price=float(sl_price),
        tp_price=float(tp_price) if tp_price is not None else None,
        magic=magic,
        comment="QE2026-phase0",
        time_utc=utc_now(),
        status=OrderStatus.PENDING,
    )


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    load_dotenv()
    shield_model = Shield()
    limits_map = shield_model.limits

    threading.Thread(target=_tick_listener, daemon=True).start()

    signal_bus = RedisBus.from_env()
    router = ExecutionRouter(signal_bus)
    startup_sleep = float(os.environ.get("EXECUTOR_TICK_WARMUP_SEC", "8"))
    if startup_sleep > 0:
        time.sleep(startup_sleep)

    for payload in signal_bus.iter_channel_messages(CHANNEL_SIGNALS_RAW):
        signal_bus.heartbeat("executor_signals")

        if not isinstance(payload, dict):
            continue

        equity_now = float(account_equity())
        start_eq, _peak_eq = sync_baselines(signal_bus, equity_now)

        chk = shield_model.check_drawdowns(start_equity=start_eq, equity=equity_now)
        if not chk.ok:
            halt_trading(signal_bus, chk.reason)
            log_event("risk_kill", {"reason": chk.reason, "equity": equity_now, "start": start_eq})

        if is_halted(signal_bus):
            continue

        sig = Signal.from_dict(dict(payload))
        pairs_all = load_pair_settings()
        cfg = pairs_all.get(sig.symbol, {})
        if not cfg.get("enabled", False):
            _LOG.warning("symbol_disabled symbol=%s", sig.symbol)
            continue

        mx_open = int(limits_map.get("max_open_positions", 999))
        if not correlation_ok(signal_bus, mx_open):
            _LOG.warning("max_open_positions_reached limit=%s", mx_open)
            continue

        order = _build_order(sig, cfg, limits_map)
        if order is None or order.volume_lots <= 0:
            continue

        res = router.dispatch(order)
        if not res.ok:
            log_event(
                "order_reject",
                {
                    "symbol": order.symbol,
                    "volume": order.volume_lots,
                    "bridge_comment": res.comment,
                },
            )


if __name__ == "__main__":
    main()
