"""
Live MT5 market data ingestion system (Phase 2).

This script:
1) Connects to MetaTrader 5 (MT5) using credentials from `core.config`.
2) Reads live ticks for ALL configured symbols once per second.
3) Publishes the data into Redis using consistent keys.
4) Publishes RAW_MARKET_DATA on the in-process event bus for tick_sanitizer.

Demo vs live market data (runtime semantics)
--------------------------------------------
There is NO separate MT5_DEMO environment flag. Prices come from whichever
account is logged into the MT5 **desktop terminal** on this machine (demo or
live). `.env` supplies login/server/symbols; SYSTEM_MODE (TEST/LIVE) is a
deployment safety switch used elsewhere — it does not replace MT5_SYMBOLS and
does not start this loop by itself.

Streamlit (`dashboard`) does not run pulse — run `python run.py` while MT5 is
open if you want live ticks through the engine.
"""

# MetaTrader5 is the official Python package for talking to the MT5 terminal.
import MetaTrader5 as mt5

# time is used for the 1-second polling loop.
import time

# json is used to build a plain dictionary payload for Redis (even though core.bus
# also JSON-serializes before storing).
import json
from datetime import datetime, timezone

from core.config import load_config
from core.logger import get_logger
from core.bus import set_value
from core.event_bus import EventType, bus
from core import system_registry as reg

log = get_logger()

# To keep logs readable, we rate-limit repeated warnings per symbol.
# Key shape: (symbol, kind) -> epoch_seconds
_last_symbol_warning_ts = {}
_SYMBOL_WARNING_COOLDOWN_SECONDS = 10


def format_price(symbol, value):
    """
    Format a price value based on symbol type.

    Rounding rules:
    - Forex pairs: 5 decimals (example: EURUSD -> 1.36275)
    - Gold/Metals: 2 decimals (example: XAUUSD -> 3312.45)
    - Crypto pairs: 2 decimals (example: BTCUSD -> 94250.12)
    """
    if value is None:
        return None

    symbol_upper = str(symbol).upper()
    numeric_value = float(value)

    # Gold/metal symbols are usually prefixed with XAU/XAG.
    if symbol_upper.startswith("XAU") or symbol_upper.startswith("XAG"):
        return round(numeric_value, 2)

    # Common crypto symbols (extendable later if needed).
    crypto_prefixes = ("BTC", "ETH", "LTC", "XRP", "SOL", "ADA", "DOGE")
    if symbol_upper.startswith(crypto_prefixes):
        return round(numeric_value, 2)

    # Default to forex precision.
    return round(numeric_value, 5)


def _best_effort_set_redis(key, value, *, silent: bool = True):
    """Write to Redis, but never crash the ingestion loop if Redis is down."""
    try:
        set_value(key, value, silent=silent)
    except Exception as exc:
        log.error(f"Redis write failed | key={key} | error={exc}")


def initialize_mt5():
    """
    Connect to MT5 and verify the connection.

    Returns:
      True if connected, otherwise False.
    """
    config = load_config()

    login = config.get("MT5_LOGIN")
    password = config.get("MT5_PASSWORD", "")
    server = config.get("MT5_SERVER", "")

    # Update Redis system status early (best effort).
    _best_effort_set_redis("mt5:connection", "initializing", silent=False)

    # If MT5_LOGIN is missing, we still try to initialize without credentials.
    # This helps beginners who already have MT5 session logged in via the terminal.
    if login is None:
        log.warning(
            "MT5_LOGIN is not set in .env. Attempting mt5.initialize() without login."
        )
        ok = mt5.initialize()
    else:
        log.info(
            "Initializing MT5 | server set=%s | login set=%s",
            bool(server),
            bool(login),
        )
        ok = mt5.initialize(login=login, password=password, server=server)

    if not ok:
        err = mt5.last_error()
        log.error(f"MT5 initialize failed | last_error={err}")
        _best_effort_set_redis("mt5:connection", "failed", silent=False)
        return False

    # terminal_info/account_info being None usually means "not really connected".
    info = mt5.terminal_info()
    if info is None:
        log.error("MT5 terminal_info() returned None after initialize().")
        _best_effort_set_redis("mt5:connection", "failed", silent=False)
        try:
            mt5.shutdown()
        except Exception:
            pass
        return False

    _best_effort_set_redis("mt5:connection", "connected", silent=False)
    log.info("MT5 connected successfully.")
    return True


def get_live_tick(symbol):
    """
    Read the latest tick for one symbol.

    Returns:
      A dict with keys: bid, ask, spread, timestamp
      or None if the symbol is missing / no tick is available.
    """
    # Ensure the symbol is selected/visible in MT5 Market Watch.
    try:
        selected = mt5.symbol_select(symbol, True)
    except Exception as exc:
        log.error(f"MT5 symbol_select failed | symbol={symbol} | error={exc}")
        return None

    if not selected:
        now = time.time()
        key = (symbol, "symbol_not_available")
        last = _last_symbol_warning_ts.get(key, 0)
        if now - last >= _SYMBOL_WARNING_COOLDOWN_SECONDS:
            log.warning(f"MT5 symbol not available | symbol={symbol}")
            _last_symbol_warning_ts[key] = now
        return None

    try:
        tick = mt5.symbol_info_tick(symbol)
    except Exception as exc:
        log.error(f"MT5 symbol_info_tick failed | symbol={symbol} | error={exc}")
        return None

    if tick is None:
        # Graceful handling: missing tick is not fatal; we just skip this symbol.
        now = time.time()
        key = (symbol, "no_tick_data")
        last = _last_symbol_warning_ts.get(key, 0)
        if now - last >= _SYMBOL_WARNING_COOLDOWN_SECONDS:
            log.warning(f"No tick data yet | symbol={symbol}")
            _last_symbol_warning_ts[key] = now
        return None

    # Build a plain payload so Redis writes are consistent.
    bid = float(getattr(tick, "bid", 0.0) or 0.0)
    ask = float(getattr(tick, "ask", 0.0) or 0.0)

    spread_value = getattr(tick, "spread", None)
    if spread_value is None:
        spread_value = ask - bid

    # MT5 provides time in seconds; sometimes there is higher precision with time_msc.
    timestamp_msc = getattr(tick, "time_msc", None)
    if timestamp_msc is not None:
        timestamp = int(timestamp_msc)
    else:
        timestamp = int(getattr(tick, "time", 0) or 0)

    payload = {
        "bid": bid,
        "ask": ask,
        "spread": float(spread_value),
        "timestamp": timestamp,
    }

    # Use json module as requested (keeps payload "plain").
    _ = json.dumps(payload)
    return payload


def publish_tick(symbol, tick):
    """
    Publish one tick to Redis under the required key structure.

    Redis keys:
      market:{symbol}:bid
      market:{symbol}:ask
      market:{symbol}:spread
      market:{symbol}:timestamp
    """
    if tick is None:
        return

    # We format prices before publishing so Redis always stores clean precision.
    bid = format_price(symbol, tick.get("bid"))
    ask = format_price(symbol, tick.get("ask"))
    # Spread uses the same symbol precision for consistent display and calculations.
    spread = format_price(symbol, tick.get("spread"))
    timestamp = tick.get("timestamp")
    event_time = _normalize_event_time(timestamp)

    base = f"market:{symbol}"
    _best_effort_set_redis(f"{base}:bid", bid)
    _best_effort_set_redis(f"{base}:ask", ask)
    _best_effort_set_redis(f"{base}:spread", spread)
    _best_effort_set_redis(f"{base}:timestamp", timestamp)

    # In-process event flow: raw ticks must go through tick_sanitizer before
    # any storage, regime, or strategy logic can consume them.
    bus.publish(
        EventType.RAW_MARKET_DATA,
        {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "time": event_time,
        },
        source="pulse",
    )


def _normalize_event_time(timestamp):
    """Convert MT5 seconds/milliseconds timestamp to ISO UTC for EventBus."""
    try:
        ts = int(timestamp or 0)
        if ts > 10_000_000_000:  # MT5 time_msc
            ts = ts / 1000.0
        if ts > 0:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        pass
    return datetime.now(timezone.utc).isoformat()


def run_pulse():
    """
    Main loop:
    - Connect to MT5
    - For ALL symbols, read tick and publish to Redis
    - Repeat once per second
    """
    _best_effort_set_redis("pulse:status", "starting", silent=False)

    if not initialize_mt5():
        _best_effort_set_redis("pulse:status", "mt5_connection_failed", silent=False)
        return

    config = load_config()
    symbols = config.get("MT5_SYMBOLS", []) or []

    if not symbols:
        log.error(
            "No MT5 symbols configured. Set MT5_SYMBOLS in .env (comma-separated broker symbols)."
        )
        _best_effort_set_redis("pulse:status", "no_symbols_configured", silent=False)
        try:
            mt5.shutdown()
        except Exception:
            pass
        return

    log.info("Starting pulse | symbols=%s | interval_seconds=1", symbols)
    _best_effort_set_redis("pulse:status", "running", silent=False)

    try:
        while True:
            start = time.time()
            saw_tick = False

            for symbol in symbols:
                tick = get_live_tick(symbol)
                if tick is not None:
                    saw_tick = True
                publish_tick(symbol, tick)

            now_ts = time.time()
            reg.touch_system_heartbeat("pulse")
            _best_effort_set_redis("pulse:heartbeat", now_ts)
            if saw_tick:
                _best_effort_set_redis("pulse:last_tick_epoch", now_ts)

            # Publish every second (roughly). We sleep the remainder of the second.
            elapsed = time.time() - start
            sleep_for = max(0.0, 1.0 - elapsed)
            time.sleep(sleep_for)

    except KeyboardInterrupt:
        log.info("Pulse stopped by user (KeyboardInterrupt).")
        _best_effort_set_redis("pulse:status", "stopped", silent=False)
    except Exception as exc:
        # If something unexpected happens, mark status and stop.
        log.error(f"Pulse crashed: {exc}")
        _best_effort_set_redis("pulse:status", "error", silent=False)
    finally:
        # Always shutdown MT5 safely.
        try:
            mt5.shutdown()
        except Exception:
            pass

        _best_effort_set_redis("mt5:connection", "disconnected", silent=False)
        log.info("MT5 connection shutdown complete.")


if __name__ == "__main__":
    run_pulse()

