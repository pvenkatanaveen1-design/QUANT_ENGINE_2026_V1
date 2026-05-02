from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.bus import RedisBus  # noqa: E402
from core.clock import utc_now  # noqa: E402
from schemas.tick import Tick  # noqa: E402

_LOG = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5  # type: ignore
except ImportError:
    mt5 = None


def _simulate_price(t: float) -> tuple[float, float]:
    wave = abs((t % 200.0) - 100.0) / 4000.0
    bid = 1.0800 + wave
    spread = float(os.environ.get("QUANT_SIM_SPREAD_PIPS", "0.00015"))
    ask = bid + spread
    return bid, ask


def stream_ticks_to_redis(bus: RedisBus, symbol: str, interval_sec: float) -> None:
    simulate = os.environ.get("QUANT_MT5_SIMULATE", "0") == "1" or mt5 is None

    if not simulate:
        login = int(os.environ.get("MT5_LOGIN", "0"))
        password = os.environ.get("MT5_PASSWORD", "")
        server = os.environ.get("MT5_SERVER", "")
        if not mt5.initialize(login=login, password=password, server=server):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"symbol_select failed for {symbol}")

    _LOG.info("pulse_start symbol=%s simulate=%s", symbol, simulate)
    t0 = time.time()
    while True:
        if simulate:
            bid, ask = _simulate_price(time.time() - t0)
            tick = Tick(symbol=symbol, bid=bid, ask=ask, time_utc=utc_now())
        else:
            q = mt5.symbol_info_tick(symbol)
            if q is None:
                time.sleep(interval_sec)
                continue
            tick = Tick(
                symbol=symbol,
                bid=float(q.bid),
                ask=float(q.ask),
                time_utc=utc_now(),
                last=float(q.last) if q.last else None,
                volume=float(q.volume) if q.volume else 0.0,
            )
        bus.publish_json(f"ticks:{symbol}", tick.to_dict())
        bus.heartbeat("pulse")
        time.sleep(interval_sec)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    load_dotenv()
    symbol = os.environ.get("SYMBOL_DEFAULT", "EURUSD")
    interval_sec = float(os.environ.get("PULSE_INTERVAL_SEC", "0.05"))
    bus = RedisBus.from_env()
    stream_ticks_to_redis(bus, symbol, interval_sec=interval_sec)


if __name__ == "__main__":
    main()
