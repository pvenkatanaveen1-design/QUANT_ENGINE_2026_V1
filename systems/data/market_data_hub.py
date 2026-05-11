"""
systems/data/market_data_hub.py — S6: Market Data Hub.

WHY THIS FILE EXISTS
--------------------
Every strategy, regime detector, and scoring engine needs candle data.
Without a central hub, each system would:
  - Request data from MT5 separately (slow, redundant API calls)
  - Cache data in different formats (DataFrames, dicts, lists)
  - Have different gap detection logic

This hub is the SINGLE SOURCE OF TRUTH for all market data:
  - Subscribes to clean MARKET_DATA events from tick_sanitizer.py
  - Stores ticks and candles in DuckDB via StorageService
  - Provides get_candles() and get_latest_tick() to any system
  - Detects data gaps and publishes DATA_GAP_DETECTED alerts
  - Imports CSV from Dukascopy for historical backtesting data

DATA FLOW:
  MT5 → core/pulse.py → EventBus(RAW_MARKET_DATA)
  → tick_sanitizer (validates and tags sanitized=True)
  → EventBus(MARKET_DATA clean)
  → market_data_hub._on_market_data() → DuckDB

QUERY INTERFACE (called by regime_detector, backtester, etc.):
  hub.get_candles("XAUUSD", "H1", n=200)  → pd.DataFrame
  hub.get_latest_tick("XAUUSD")           → {"bid": 2345.50, "ask": 2345.70}

2026 XAUUSD DATA NOTES:
  Dukascopy tick data for XAUUSD is available free:
  https://www.dukascopy.com/swiss/english/marketwatch/historical/
  Download 6 months minimum for a valid backtest (200+ trades needed).
  Format: CSV with Date, Time, Ask, Bid, Ask Volume, Bid Volume columns.
"""

from __future__ import annotations

import csv
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core.event_bus import EventType, bus
from core.logger import get_logger, LogCategory
from services.storage_service import StorageService

log = get_logger("market_data_hub", LogCategory.DATA)

# DuckDB does not support pd.DataFrame natively without pandas import.
# We lazy-import pandas so this module works even if pandas is not installed
# (tests might run without it).
try:
    import pandas as pd
    _PANDAS_AVAILABLE = True
except ImportError:
    _PANDAS_AVAILABLE = False

# Maximum ticks to keep in memory per symbol (for fast latest-tick access)
_TICK_CACHE_SIZE = 500

# Candle aggregation intervals supported
SUPPORTED_TIMEFRAMES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}


class MarketDataHub:
    """
    Central market data repository.

    Singleton — import via:
        from systems.data.market_data_hub import hub

    On startup:
        hub.start()  # subscribes to EventBus, begins accepting ticks

    For strategies and regime detector:
        candles = hub.get_candles("XAUUSD", "H1", n=200)  # pd.DataFrame
        tick    = hub.get_latest_tick("XAUUSD")            # dict

    For CSV import (historical data):
        hub.import_dukascopy_csv("/path/to/xauusd_h1.csv", "XAUUSD", "H1")
    """

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage

        # In-memory tick cache: symbol → deque of latest clean tick dicts
        from collections import deque
        self._tick_cache: dict[str, deque] = {}
        self._cache_lock = threading.Lock()

        # Candle construction state: symbol+timeframe → partial candle dict
        self._partial_candles: dict[str, dict] = {}
        self._candle_lock = threading.Lock()

        # Data quality: count of ticks and gaps per symbol today
        self._tick_counts: dict[str, int] = {}
        self._gap_counts:  dict[str, int] = {}
        self._last_tick_time: dict[str, datetime] = {}
        self._running = False

        log.info("MarketDataHub initialized")

    def start(self) -> None:
        """
        Subscribe to clean MARKET_DATA events and begin processing.
        Call this once during system startup after tick_sanitizer.start().
        """
        bus.subscribe(EventType.MARKET_DATA, self._on_market_data)
        self._running = True
        log.info("MarketDataHub started — subscribed to MARKET_DATA")

    def stop(self) -> None:
        """Unsubscribe from events.  Call during graceful shutdown."""
        bus.unsubscribe(EventType.MARKET_DATA, self._on_market_data)
        self._running = False
        log.info("MarketDataHub stopped")

    # ─── EVENT HANDLER ────────────────────────────────────────────────────────

    def _on_market_data(self, event) -> None:
        """
        Called for every clean tick arriving from tick_sanitizer.
        Payload expected: dict with keys:
            symbol, bid, ask, time (ISO string or datetime), sanitized=True

        This handler runs in the ThreadPoolExecutor (async dispatch).
        It must be thread-safe.
        """
        try:
            payload = event.payload
            if not isinstance(payload, dict):
                return
            if payload.get("sanitized") is not True:
                log.warning(
                    "MarketDataHub rejected unsanitized MARKET_DATA payload",
                    source=getattr(event, "source", "unknown"),
                    event_type=getattr(event, "event_type", "MARKET_DATA"),
                )
                return

            symbol = payload.get("symbol", "")
            bid    = float(payload.get("bid", 0.0))
            ask    = float(payload.get("ask", 0.0))
            time_raw = payload.get("time")

            if not symbol or bid <= 0 or ask <= 0:
                return

            # Normalize timestamp
            if isinstance(time_raw, str):
                tick_time = datetime.fromisoformat(time_raw)
            elif isinstance(time_raw, datetime):
                tick_time = time_raw
            else:
                tick_time = datetime.utcnow()

            spread_pips = round((ask - bid) / 0.10, 2)  # XAUUSD: 1 pip = 0.10

            tick_dict = {
                "symbol":      symbol,
                "time":        tick_time,
                "bid":         bid,
                "ask":         ask,
                "spread_pips": spread_pips,
            }

            # Update in-memory cache
            self._update_tick_cache(symbol, tick_dict)

            # Persist to DuckDB (non-blocking; DuckDB lock is inside storage_service)
            self._persist_tick(tick_dict)

            # Update candle aggregation for all timeframes
            self._aggregate_candle(symbol, tick_dict)

            # Track quality metrics
            with self._cache_lock:
                self._tick_counts[symbol] = self._tick_counts.get(symbol, 0) + 1
                self._last_tick_time[symbol] = tick_time

        except Exception as exc:
            log.error(f"MarketDataHub tick error: {exc}", exc_info=True)

    def _update_tick_cache(self, symbol: str, tick: dict) -> None:
        """Update the in-memory tick ring buffer for fast latest-tick access."""
        from collections import deque
        with self._cache_lock:
            if symbol not in self._tick_cache:
                self._tick_cache[symbol] = deque(maxlen=_TICK_CACHE_SIZE)
            self._tick_cache[symbol].append(tick)

    def _persist_tick(self, tick: dict) -> None:
        """Write tick to DuckDB ticks table."""
        self._storage.execute_duckdb_write(
            "INSERT INTO ticks (symbol, time, bid, ask, spread_pips) VALUES (?,?,?,?,?)",
            (tick["symbol"], tick["time"], tick["bid"], tick["ask"], tick["spread_pips"]),
        )

    def _aggregate_candle(self, symbol: str, tick: dict) -> None:
        """
        Build OHLCV candles in real-time from tick stream.
        Creates a candle for each configured timeframe.
        When a candle period closes, saves to DuckDB and publishes CANDLE_CLOSED.
        """
        tick_time = tick["time"]
        mid_price = (tick["bid"] + tick["ask"]) / 2.0

        for timeframe, minutes in SUPPORTED_TIMEFRAMES.items():
            key = f"{symbol}_{timeframe}"

            # Calculate the start of the current candle period
            ts = tick_time.replace(second=0, microsecond=0)
            if minutes >= 60:
                hours = minutes // 60
                period_start = ts.replace(minute=0) - timedelta(
                    hours=(ts.hour % hours)
                )
            else:
                period_start = ts - timedelta(minutes=(ts.minute % minutes))

            with self._candle_lock:
                if key not in self._partial_candles:
                    # Start a fresh candle
                    self._partial_candles[key] = {
                        "symbol":    symbol,
                        "timeframe": timeframe,
                        "time":      period_start,
                        "open":      mid_price,
                        "high":      mid_price,
                        "low":       mid_price,
                        "close":     mid_price,
                        "volume":    1,
                    }
                    continue

                candle = self._partial_candles[key]

                if period_start > candle["time"]:
                    # New period → close the old candle
                    completed = candle.copy()
                    self._partial_candles[key] = {
                        "symbol":    symbol,
                        "timeframe": timeframe,
                        "time":      period_start,
                        "open":      mid_price,
                        "high":      mid_price,
                        "low":       mid_price,
                        "close":     mid_price,
                        "volume":    1,
                    }

                    # Persist completed candle
                    self._persist_candle(completed)

                    # Publish CANDLE_CLOSED (only for H1 to trigger regime detector)
                    if timeframe == "H1":
                        bus.publish(
                            EventType.CANDLE_CLOSED,
                            completed,
                            source="market_data_hub",
                        )
                else:
                    # Same period → update OHLCV
                    candle["high"]   = max(candle["high"], mid_price)
                    candle["low"]    = min(candle["low"],  mid_price)
                    candle["close"]  = mid_price
                    candle["volume"] += 1

    def _persist_candle(self, candle: dict) -> None:
        """Write a completed candle to DuckDB."""
        self._storage.execute_duckdb_write(
            """INSERT OR IGNORE INTO candles
               (symbol, timeframe, time, open, high, low, close, volume)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                candle["symbol"], candle["timeframe"], candle["time"],
                candle["open"],   candle["high"],      candle["low"],
                candle["close"],  candle["volume"],
            ),
        )

        # Gap detection: check if previous candle exists
        self._check_gap(candle)

    def _check_gap(self, new_candle: dict) -> None:
        """
        After storing a candle, check if the previous expected candle exists.
        If not → publish DATA_GAP_DETECTED for the quality monitor to handle.
        """
        try:
            symbol    = new_candle["symbol"]
            timeframe = new_candle["timeframe"]
            minutes   = SUPPORTED_TIMEFRAMES.get(timeframe, 60)
            expected_prev = new_candle["time"] - timedelta(minutes=minutes)

            rows = self._storage.execute_duckdb(
                "SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe=? AND time=?",
                (symbol, timeframe, expected_prev),
            )
            count = rows[0][0] if rows else 0
            if count == 0:
                gap_key = f"{symbol}_{timeframe}"
                with self._cache_lock:
                    self._gap_counts[gap_key] = self._gap_counts.get(gap_key, 0) + 1

                bus.publish(
                    EventType.DATA_GAP_DETECTED,
                    {
                        "symbol":        symbol,
                        "timeframe":     timeframe,
                        "missing_time":  expected_prev.isoformat(),
                        "next_candle":   new_candle["time"].isoformat(),
                    },
                    source="market_data_hub",
                )
        except Exception as exc:
            log.debug(f"Gap check error (non-critical): {exc}")

    # ─── PUBLIC QUERY API ─────────────────────────────────────────────────────

    def get_candles(
        self,
        symbol:    str,
        timeframe: str = "H1",
        n:         int = 200,
    ):
        """
        Return last N candles for a symbol as a pandas DataFrame.

        Columns: time, open, high, low, close, volume
        Sorted: oldest to newest (index 0 = oldest, index -1 = latest).

        If pandas is not installed, returns a list of dicts instead.

        Parameters:
            symbol:    e.g. "XAUUSD"
            timeframe: e.g. "H1", "M15", "D1"
            n:         number of candles to return (default 200)
        """
        rows = self._storage.execute_duckdb(
            """SELECT time, open, high, low, close, volume
               FROM candles
               WHERE symbol = ? AND timeframe = ?
               ORDER BY time DESC LIMIT ?""",
            (symbol, timeframe, n),
        )

        if not rows:
            log.warning(f"No candle data for {symbol} {timeframe} — import historical data first")
            if _PANDAS_AVAILABLE:
                return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
            return []

        # Reverse so oldest is first (standard for TA calculations)
        rows = rows[::-1]
        cols = ["time", "open", "high", "low", "close", "volume"]

        if _PANDAS_AVAILABLE:
            df = pd.DataFrame(rows, columns=cols)
            df["time"] = pd.to_datetime(df["time"])
            df = df.set_index("time")
            return df

        return [dict(zip(cols, r)) for r in rows]

    def get_latest_tick(self, symbol: str) -> Optional[dict]:
        """
        Return the most recent tick for a symbol.
        Used by cost_guard (live spread check) and signal generators (entry price).

        Returns: {"symbol", "bid", "ask", "spread_pips", "time"} or None
        """
        with self._cache_lock:
            cache = self._tick_cache.get(symbol)
            if cache:
                return dict(cache[-1])

        # Fallback: query DuckDB
        rows = self._storage.execute_duckdb(
            "SELECT symbol, time, bid, ask, spread_pips FROM ticks "
            "WHERE symbol = ? ORDER BY time DESC LIMIT 1",
            (symbol,),
        )
        if rows:
            cols = ["symbol", "time", "bid", "ask", "spread_pips"]
            return dict(zip(cols, rows[0]))
        return None

    # ─── CSV IMPORT ──────────────────────────────────────────────────────────

    def import_dukascopy_csv(
        self,
        csv_path:  str,
        symbol:    str,
        timeframe: str = "H1",
    ) -> int:
        """
        Import historical OHLCV data from a Dukascopy CSV file.

        Dukascopy CSV format (tick data):
            Date,Time,Ask,Bid,AskVolume,BidVolume
            or
            Date,Time,Open,High,Low,Close,Volume

        Returns number of rows imported.

        USAGE:
            hub.import_dukascopy_csv("data/raw/xauusd_h1_2024.csv", "XAUUSD", "H1")

        Download from:
            https://www.dukascopy.com/swiss/english/marketwatch/historical/
        """
        path = Path(csv_path)
        if not path.exists():
            log.error(f"CSV not found: {csv_path}")
            return 0

        rows_to_insert = []
        skipped = 0

        try:
            with open(path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames or []
                log.info(f"CSV headers: {headers}")

                for row in reader:
                    try:
                        # Try OHLCV format first
                        if "Open" in headers or "open" in headers:
                            # Standard OHLCV format
                            date_str = row.get("Date", row.get("date", ""))
                            time_str = row.get("Time", row.get("time", "00:00:00"))
                            dt_str = f"{date_str} {time_str}"
                            dt = datetime.strptime(dt_str.strip(), "%Y.%m.%d %H:%M:%S")
                            rows_to_insert.append((
                                symbol, timeframe, dt,
                                float(row.get("Open",  row.get("open",  0))),
                                float(row.get("High",  row.get("high",  0))),
                                float(row.get("Low",   row.get("low",   0))),
                                float(row.get("Close", row.get("close", 0))),
                                float(row.get("Volume", row.get("volume", 0))),
                            ))
                        else:
                            # Tick format — use mid price as OHLCV stub
                            date_str = row.get("Date", "")
                            time_str = row.get("Time", "00:00:00")
                            dt_str = f"{date_str} {time_str}"
                            dt = datetime.strptime(dt_str.strip(), "%Y.%m.%d %H:%M:%S")
                            ask = float(row.get("Ask", 0))
                            bid = float(row.get("Bid", 0))
                            mid = (ask + bid) / 2.0
                            rows_to_insert.append((
                                symbol, timeframe, dt,
                                mid, mid, mid, mid, 1,
                            ))
                    except Exception as row_exc:
                        skipped += 1
                        log.debug(f"CSV row skip: {row_exc}")

        except Exception as exc:
            log.error(f"CSV read error: {exc}", exc_info=True)
            return 0

        # Bulk insert
        if rows_to_insert:
            sql = """INSERT OR IGNORE INTO candles
                     (symbol, timeframe, time, open, high, low, close, volume)
                     VALUES (?,?,?,?,?,?,?,?)"""
            try:
                with self._storage._duckdb_lock:
                    self._storage._duckdb_conn.executemany(sql, rows_to_insert)
            except Exception as exc:
                log.error(f"CSV bulk insert error: {exc}", exc_info=True)
                return 0

        imported = len(rows_to_insert)
        log.info(
            f"CSV import complete: {imported} rows for {symbol} {timeframe}. "
            f"Skipped {skipped} rows."
        )
        return imported

    # ─── STATS / DIAGNOSTICS ─────────────────────────────────────────────────

    def get_coverage(self, symbol: str, timeframe: str = "H1") -> dict:
        """
        Return data coverage summary for a symbol.
        Used by dashboard Market Data page.

        Returns: {symbol, timeframe, total_candles, first_candle, last_candle,
                  data_gap_count, tick_count_today}
        """
        rows = self._storage.execute_duckdb(
            """SELECT COUNT(*), MIN(time), MAX(time)
               FROM candles WHERE symbol = ? AND timeframe = ?""",
            (symbol, timeframe),
        )
        total, first, last = (rows[0] if rows else (0, None, None))

        with self._cache_lock:
            gaps  = self._gap_counts.get(f"{symbol}_{timeframe}", 0)
            ticks = self._tick_counts.get(symbol, 0)

        return {
            "symbol":          symbol,
            "timeframe":       timeframe,
            "total_candles":   total or 0,
            "first_candle":    str(first) if first else "N/A",
            "last_candle":     str(last)  if last  else "N/A",
            "data_gap_count":  gaps,
            "tick_count_today": ticks,
        }

    def get_stats(self) -> dict:
        """Return summary stats for dashboard Market Data page."""
        stats: dict = {
            "running":     self._running,
            "symbols":     list(self._tick_cache.keys()),
            "tick_counts": dict(self._tick_counts),
            "gap_counts":  dict(self._gap_counts),
        }
        # Add coverage for each symbol
        stats["coverage"] = {
            sym: self.get_coverage(sym, "H1")
            for sym in stats["symbols"]
        }
        return stats


# ─── SINGLETON ────────────────────────────────────────────────────────────────
# Initialize once using the shared storage singleton.
from services.storage_service import storage as _storage
hub = MarketDataHub(_storage)
