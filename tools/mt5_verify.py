#!/usr/bin/env python3
"""
One-shot MT5 market data diagnostic.

Run from the project root (QUANT_ENGINE_2026) after MetaTrader 5 is open:
  python tools/mt5_verify.py

Uses MT5_LOGIN / MT5_PASSWORD / MT5_SERVER from `.env`.
Symbol: SYMBOL_DEFAULT (default EURUSD).
Exit: 0 if at least one tick was received; 1 otherwise.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def _pip_size(si) -> float:
    if si is None:
        return 1e-4
    pt = float(si.point)
    d = int(si.digits)
    if d in (3, 5):
        return pt * 10.0
    return pt


def _tick_msc(tick) -> int:
    msc = getattr(tick, "time_msc", None)
    if msc is not None:
        return int(msc)
    return int(getattr(tick, "time", 0) or 0) * 1000


def _init_mt5() -> bool:
    import MetaTrader5 as mt5

    login_raw = os.getenv("MT5_LOGIN", "").strip()
    server = os.getenv("MT5_SERVER", "").strip()
    password = os.getenv("MT5_PASSWORD", "")

    login = int(login_raw) if login_raw else None

    if login is None:
        print("MT5_LOGIN not set — trying mt5.initialize() with existing terminal session.")
        ok = mt5.initialize()
    else:
        print(f"Connecting MT5 | server={server!r} | login set={bool(login)}")
        ok = mt5.initialize(login=login, password=password, server=server)

    if not ok:
        print(f"mt5.initialize() failed | last_error={mt5.last_error()}", file=sys.stderr)
        return False
    if mt5.terminal_info() is None:
        print("mt5.terminal_info() is None after initialize().", file=sys.stderr)
        try:
            mt5.shutdown()
        except Exception:
            pass
        return False
    return True


def main() -> int:
    _load_env()

    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("Install MetaTrader5: pip install MetaTrader5", file=sys.stderr)
        return 1

    symbol = os.getenv("SYMBOL_DEFAULT", "EURUSD").strip().upper()
    if not _init_mt5():
        return 1

    try:
        if not mt5.symbol_select(symbol, True):
            print(f"symbol_select failed or symbol unavailable: {symbol}", file=sys.stderr)
            return 1

        si = mt5.symbol_info(symbol)
        pip = _pip_size(si)

        print(f"Watching {symbol} for 10 seconds (pip size for spread = {pip})")
        print(f"{'timestamp':<26} | {'bid':>12} | {'ask':>12} | {'spread_pips':>12} | {'tick_count':>10}")
        print("-" * 90)

        total_ticks = 0
        spreads_pips: list[float] = []
        last_msc: int | None = None
        run_until = time.monotonic() + 10.0

        while time.monotonic() < run_until:
            sec_end = min(time.monotonic() + 1.0, run_until)
            tick_count_sec = 0
            last_bid = last_ask = 0.0
            wall_ts = ""

            while time.monotonic() < sec_end:
                tick = mt5.symbol_info_tick(symbol)
                if tick is not None:
                    bid = float(getattr(tick, "bid", 0.0) or 0.0)
                    ask = float(getattr(tick, "ask", 0.0) or 0.0)
                    last_bid, last_ask = bid, ask
                    msc = _tick_msc(tick)
                    tsec = getattr(tick, "time", None)
                    if tsec:
                        wall_ts = datetime.fromtimestamp(int(tsec), tz=timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S UTC"
                        )
                    if last_msc is not None and msc != last_msc:
                        tick_count_sec += 1
                        total_ticks += 1
                        sp = (ask - bid) / pip if pip > 0 else 0.0
                        spreads_pips.append(float(sp))
                    last_msc = msc
                time.sleep(0.001)

            sp_reading = (last_ask - last_bid) / pip if pip > 0 else 0.0
            ts_col = wall_ts or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            print(
                f"{ts_col:<26} | {last_bid:12.5f} | {last_ask:12.5f} | {sp_reading:12.2f} | {tick_count_sec:10d}"
            )

        print("-" * 90)
        rate = total_ticks / 10.0
        health = "OK" if rate > 0.5 else "WARN"

        print("Summary:")
        print(f"  total_ticks (new quotes): {total_ticks}")
        if spreads_pips:
            avg_sp = sum(spreads_pips) / len(spreads_pips)
            mn, mx = min(spreads_pips), max(spreads_pips)
            print(f"  avg spread (pips, over new ticks): {avg_sp:.2f}")
            print(f"  min / max spread (pips): {mn:.2f} / {mx:.2f}")
        else:
            print("  avg / min / max spread (pips): n/a (no new ticks during window)")
        print(f"  tick rate: {rate:.3f} ticks/sec  ->  {health}  (threshold 0.5 ticks/sec)")

        return 0 if total_ticks > 0 else 1
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
