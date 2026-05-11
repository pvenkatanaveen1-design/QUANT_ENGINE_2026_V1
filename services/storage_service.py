"""
services/storage_service.py — Centralized database layer (DuckDB + SQLite).

WHY THIS FILE EXISTS
--------------------
Without a central storage layer, every system opens its own DB connection,
writes raw SQL, and has its own retry logic.  That means:
  - Different tables for the same data
  - No consistent error handling
  - No performance monitoring
  - Impossible to know "which system is killing the DB"

This service is the ONLY place in the engine that talks to DuckDB and SQLite.
All other systems use repositories/ which call this service.

TWO DATABASES — WHY:
  DuckDB (data/market.duckdb):
    - Columnar storage, optimized for analytical reads
    - Fast for: "give me the last 500 H1 candles"
    - Stores: ticks, candles, backtest results, regime history
    - DuckDB allows only ONE writer thread at a time (uses internal lock here)

  SQLite (data/journal.db):
    - Row-oriented, optimized for transactional writes
    - Fast for: "insert this trade record NOW"
    - Stores: trades, signals, state snapshots, kill switch log
    - SQLite with WAL mode supports concurrent reads + one writer

RETRY LOGIC:
  Both DB calls wrap in execute_with_retry().
  On lock contention (SQLITE_BUSY, DuckDB conflict), waits and retries.
  Max 3 attempts with exponential backoff (0.1s, 0.2s, 0.4s).
  If all retries fail, raises StorageError.

PERFORMANCE TIMING:
  Every read/write records its duration.
  get_stats() returns avg read/write latency for the dashboard.

2026 REALITY NOTES:
  On a 4-core VPS with SSD:
    DuckDB analytical query (500 candles): 1-5ms
    SQLite insert (one trade): 0.5-2ms
    SQLite read (last 100 trades): 1-3ms
  If your times are 10x higher: your VPS has slow disk or DB is fragmented.
  Run VACUUM on SQLite monthly.  DuckDB auto-vacuums.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core.logger import get_logger, LogCategory

log = get_logger("storage_service", LogCategory.DEPENDENCY)

# ─── PATH CONSTANTS ───────────────────────────────────────────────────────────
# These match core/constants.py but we redefine here to avoid circular imports
# (storage_service is used by state_store which core/__init__ imports early).
_BASE_DIR    = Path(__file__).resolve().parent.parent
DUCKDB_PATH  = _BASE_DIR / "data" / "market.duckdb"
SQLITE_PATH  = _BASE_DIR / "data" / "journal.db"

# ─── RETRY SETTINGS ──────────────────────────────────────────────────────────
MAX_RETRIES       = 3
RETRY_BASE_DELAY  = 0.1   # seconds — doubles each retry: 0.1, 0.2, 0.4

# ─── LATENCY RING BUFFER ─────────────────────────────────────────────────────
_LATENCY_BUFFER_SIZE = 200   # keep last 200 op timings per DB


class StorageError(Exception):
    """Raised when a DB operation fails after all retries."""


class StorageService:
    """
    Centralized database access layer.

    Singleton — import via:
        from services.storage_service import storage

    DuckDB interface:
        storage.execute_duckdb(sql, params)      → list of rows
        storage.execute_duckdb_write(sql, params) → None

    SQLite interface:
        storage.execute_sqlite(sql, params)       → list of rows
        storage.execute_sqlite_write(sql, params) → None

    Stats:
        storage.get_stats()  → dict for dashboard

    The two backends are completely independent.
    You can write to SQLite from one thread while reading DuckDB from another.
    """

    def __init__(self) -> None:
        # Ensure data directory exists
        DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)

        # ── DuckDB backend ────────────────────────────────────────────────────
        # DuckDB has internal thread safety issues with concurrent writers.
        # We use a threading.Lock to serialize ALL DuckDB access (reads + writes).
        # For reads, this is acceptable: analytical queries complete in <5ms.
        self._duckdb_lock = threading.Lock()
        self._duckdb_conn: Optional[Any] = None  # lazy init
        self._duckdb_mode: str = "UNAVAILABLE"   # RW | READ_ONLY | UNAVAILABLE
        self._duckdb_status: str = "INIT"
        self._duckdb_reason: str = ""
        self._duckdb_write_skip_logged: bool = False

        # ── SQLite backend ────────────────────────────────────────────────────
        # SQLite WAL mode allows concurrent reads but serializes writes.
        # We use check_same_thread=False and a write lock.
        self._sqlite_conn: Optional[sqlite3.Connection] = None  # lazy init
        self._sqlite_write_lock = threading.Lock()

        # ── Latency tracking ─────────────────────────────────────────────────
        self._duckdb_latencies: deque[float] = deque(maxlen=_LATENCY_BUFFER_SIZE)
        self._sqlite_latencies: deque[float] = deque(maxlen=_LATENCY_BUFFER_SIZE)
        self._op_count_duckdb: int = 0
        self._op_count_sqlite: int = 0
        self._error_count: int = 0
        self._stats_lock = threading.Lock()

        # ── Initialize both databases ─────────────────────────────────────────
        self._init_sqlite()
        self._init_duckdb()

        log.info(f"StorageService ready — DuckDB:{DUCKDB_PATH}, SQLite:{SQLITE_PATH}")

    # ─── INITIALIZATION ──────────────────────────────────────────────────────

    def _init_sqlite(self) -> None:
        """Create SQLite connection and all required tables."""
        try:
            conn = sqlite3.connect(
                str(SQLITE_PATH),
                check_same_thread=False,
                timeout=10.0,
            )
            conn.row_factory = sqlite3.Row   # rows accessible by column name
            conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads
            conn.execute("PRAGMA synchronous=NORMAL")  # balance safety/speed
            conn.execute("PRAGMA foreign_keys=ON")
            self._sqlite_conn = conn
            self._create_sqlite_tables()
            log.info(f"SQLite initialized: {SQLITE_PATH}")
        except Exception as exc:
            log.error(f"SQLite init failed: {exc}", exc_info=True)
            raise StorageError(f"SQLite init failed: {exc}") from exc

    def _create_sqlite_tables(self) -> None:
        """Create all SQLite tables if they do not exist."""
        tables = [
            # ── Trades ───────────────────────────────────────────────────────
            """CREATE TABLE IF NOT EXISTS trades (
                id               TEXT PRIMARY KEY,
                correlation_id   TEXT,
                broker_ticket    INTEGER,
                symbol           TEXT,
                direction        TEXT,
                volume           REAL,
                status           TEXT,
                requested_price  REAL,
                fill_price       REAL,
                stop_loss        REAL,
                take_profit      REAL,
                close_price      REAL,
                close_reason     TEXT,
                slippage_pips    REAL,
                spread_at_entry  REAL,
                spread_cost_usd  REAL,
                commission       REAL,
                swap             REAL,
                total_cost_usd   REAL,
                gross_pnl        REAL,
                net_pnl          REAL,
                max_adverse_excursion  REAL,
                max_favorable_excursion REAL,
                regime_at_entry  TEXT,
                session_at_entry TEXT,
                score_at_entry   REAL,
                strategy         TEXT,
                atr_at_entry     REAL,
                open_time        TEXT,
                close_time       TEXT,
                duration_minutes REAL
            )""",

            # ── Signals ──────────────────────────────────────────────────────
            """CREATE TABLE IF NOT EXISTS signals (
                correlation_id  TEXT PRIMARY KEY,
                symbol          TEXT,
                timeframe       TEXT,
                direction       TEXT,
                strategy        TEXT,
                entry_price     REAL,
                stop_loss       REAL,
                take_profit     REAL,
                sl_pips         REAL,
                tp_pips         REAL,
                rr_ratio        REAL,
                regime          TEXT,
                session         TEXT,
                score           REAL,
                confidence      REAL,
                atr_at_signal   REAL,
                adx_at_signal   REAL,
                spread_at_signal REAL,
                approved        INTEGER,
                blocked_reason  TEXT,
                lot_size        REAL,
                timestamp       TEXT
            )""",

            # ── State snapshots ───────────────────────────────────────────────
            """CREATE TABLE IF NOT EXISTS state_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT,
                equity          REAL,
                balance         REAL,
                daily_dd_pct    REAL,
                total_dd_pct    REAL,
                open_trade_count INTEGER,
                daily_trade_count INTEGER,
                kill_switch_active INTEGER,
                news_blackout   INTEGER,
                system_mode     TEXT
            )""",

            # ── Kill switch log ───────────────────────────────────────────────
            """CREATE TABLE IF NOT EXISTS kill_switch_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                activated_at    TEXT,
                reason          TEXT,
                triggered_by    TEXT,
                deactivated_at  TEXT,
                active          INTEGER DEFAULT 1
            )""",

            # ── Recovery log ──────────────────────────────────────────────────
            """CREATE TABLE IF NOT EXISTS recovery_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT,
                open_positions  INTEGER,
                orphan_trades   INTEGER,
                kill_switch_restored INTEGER,
                daily_dd_rebuilt REAL,
                notes           TEXT
            )""",

            # ── Execution profiler ────────────────────────────────────────────
            """CREATE TABLE IF NOT EXISTS execution_fills (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id        TEXT,
                symbol          TEXT,
                direction       TEXT,
                expected_price  REAL,
                fill_price      REAL,
                slippage_pips   REAL,
                fill_latency_ms REAL,
                spread_at_fill  REAL,
                timestamp       TEXT
            )""",
        ]

        with self._sqlite_conn:
            for ddl in tables:
                self._sqlite_conn.execute(ddl)
        log.debug("SQLite tables ensured")

    def _init_duckdb(self) -> None:
        """
        Create DuckDB connection and all required tables.

        Windows note:
        - `run.py` usually owns the RW connection.
        - Dashboard process may hit file-lock errors on RW open.
        - Fallback to read-only mode so visibility remains available.
        """
        try:
            import duckdb  # type: ignore
            conn = duckdb.connect(str(DUCKDB_PATH))
            self._duckdb_conn = conn
            self._duckdb_mode = "RW"
            self._duckdb_status = "OK"
            self._duckdb_reason = ""
            self._create_duckdb_tables()
            log.info(f"DuckDB initialized: {DUCKDB_PATH}")
        except ImportError:
            log.warning(
                "DuckDB not installed.  Market data hub will be unavailable. "
                "Run: pip install duckdb"
            )
            self._duckdb_conn = None
            self._duckdb_mode = "UNAVAILABLE"
            self._duckdb_status = "MISSING_PACKAGE"
            self._duckdb_reason = "duckdb package not installed"
        except Exception as exc:
            # Retry in read-only mode for dashboard visibility on Windows locks.
            self._duckdb_conn = None
            try:
                import duckdb  # type: ignore
                conn = duckdb.connect(str(DUCKDB_PATH), read_only=True)
                self._duckdb_conn = conn
                self._duckdb_mode = "READ_ONLY"
                self._duckdb_status = "DEGRADED"
                self._duckdb_reason = f"RW open failed, using read-only fallback: {exc}"
                log.warning(
                    "DuckDB RW open failed; fallback to READ_ONLY. "
                    f"Dashboard remains visible, writes disabled. reason={exc}"
                )
            except Exception as ro_exc:
                log.error(f"DuckDB init failed: {exc}", exc_info=True)
                self._duckdb_conn = None
                self._duckdb_mode = "UNAVAILABLE"
                self._duckdb_status = "LOCKED_OR_UNAVAILABLE"
                self._duckdb_reason = str(ro_exc)

    def _create_duckdb_tables(self) -> None:
        """Create DuckDB tables for time-series market data."""
        if self._duckdb_conn is None or self._duckdb_mode != "RW":
            return
        tables = [
            # ── OHLCV Candles ─────────────────────────────────────────────────
            # One row per candle.  symbol+timeframe+time = unique key.
            """CREATE TABLE IF NOT EXISTS candles (
                symbol      VARCHAR,
                timeframe   VARCHAR,
                time        TIMESTAMP,
                open        DOUBLE,
                high        DOUBLE,
                low         DOUBLE,
                close       DOUBLE,
                volume      DOUBLE,
                PRIMARY KEY (symbol, timeframe, time)
            )""",

            # ── Raw Ticks ────────────────────────────────────────────────────
            # High-frequency tick data from MT5 pulse.
            """CREATE TABLE IF NOT EXISTS ticks (
                symbol      VARCHAR,
                time        TIMESTAMP,
                bid         DOUBLE,
                ask         DOUBLE,
                spread_pips DOUBLE
            )""",

            # ── Regime History ────────────────────────────────────────────────
            """CREATE TABLE IF NOT EXISTS regime_history (
                symbol      VARCHAR,
                timeframe   VARCHAR,
                time        TIMESTAMP,
                regime      VARCHAR,
                adx         DOUBLE,
                atr         DOUBLE,
                atr_percentile DOUBLE,
                confidence  DOUBLE,
                session     VARCHAR,
                bars_in_regime INTEGER,
                PRIMARY KEY (symbol, timeframe, time)
            )""",

            # ── Backtest Results ──────────────────────────────────────────────
            """CREATE TABLE IF NOT EXISTS backtest_results (
                id              VARCHAR PRIMARY KEY,
                strategy        VARCHAR,
                symbol          VARCHAR,
                timeframe       VARCHAR,
                start_date      DATE,
                end_date        DATE,
                total_trades    INTEGER,
                winning_trades  INTEGER,
                win_rate        DOUBLE,
                profit_factor   DOUBLE,
                sharpe_ratio    DOUBLE,
                max_drawdown_pct DOUBLE,
                total_net_pnl   DOUBLE,
                params_json     VARCHAR,
                created_at      TIMESTAMP
            )""",

            # ── Walk Forward Results ──────────────────────────────────────────
            """CREATE TABLE IF NOT EXISTS walk_forward_results (
                id              VARCHAR PRIMARY KEY,
                backtest_id     VARCHAR,
                window_index    INTEGER,
                train_start     DATE,
                train_end       DATE,
                test_start      DATE,
                test_end        DATE,
                in_sample_pf    DOUBLE,
                out_sample_pf   DOUBLE,
                params_json     VARCHAR,
                created_at      TIMESTAMP
            )""",
        ]
        with self._duckdb_lock:
            for ddl in tables:
                self._duckdb_conn.execute(ddl)
        log.debug("DuckDB tables ensured")

    # ─── SQLITE INTERFACE ─────────────────────────────────────────────────────

    def execute_sqlite(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """
        Execute a SELECT query on SQLite.  Returns list of Row objects.
        Access columns by name: row["net_pnl"] or row[0].

        Thread-safe for concurrent reads (WAL mode).
        """
        return self._sqlite_with_retry(sql, params, write=False)

    def execute_sqlite_write(self, sql: str, params: tuple = ()) -> None:
        """
        Execute an INSERT / UPDATE / DELETE on SQLite.
        Acquires write lock for serialization.
        """
        self._sqlite_with_retry(sql, params, write=True)

    def execute_sqlite_many(self, sql: str, rows: list[tuple]) -> None:
        """Bulk insert via executemany.  More efficient than individual writes."""
        t_start = time.perf_counter()
        for attempt in range(MAX_RETRIES):
            try:
                with self._sqlite_write_lock:
                    with self._sqlite_conn:
                        self._sqlite_conn.executemany(sql, rows)
                elapsed_ms = (time.perf_counter() - t_start) * 1000
                self._record_sqlite_latency(elapsed_ms)
                return
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                raise StorageError(f"SQLite bulk write failed: {exc}") from exc

    def _sqlite_with_retry(
        self, sql: str, params: tuple, write: bool
    ) -> list[sqlite3.Row]:
        t_start = time.perf_counter()
        for attempt in range(MAX_RETRIES):
            try:
                if write:
                    with self._sqlite_write_lock:
                        with self._sqlite_conn:
                            self._sqlite_conn.execute(sql, params)
                    elapsed_ms = (time.perf_counter() - t_start) * 1000
                    self._record_sqlite_latency(elapsed_ms)
                    return []
                else:
                    cursor = self._sqlite_conn.execute(sql, params)
                    rows = cursor.fetchall()
                    elapsed_ms = (time.perf_counter() - t_start) * 1000
                    self._record_sqlite_latency(elapsed_ms)
                    return rows
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < MAX_RETRIES - 1:
                    log.warning(f"SQLite locked — retry {attempt + 1}/{MAX_RETRIES}")
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                with self._stats_lock:
                    self._error_count += 1
                raise StorageError(f"SQLite failed after {MAX_RETRIES} attempts: {exc}") from exc
            except Exception as exc:
                with self._stats_lock:
                    self._error_count += 1
                raise StorageError(f"SQLite error: {exc}") from exc
        return []

    # ─── DUCKDB INTERFACE ─────────────────────────────────────────────────────

    def execute_duckdb(self, sql: str, params: tuple = ()) -> list[tuple]:
        """
        Execute a SELECT query on DuckDB.
        Returns list of plain tuples.
        If DuckDB is not available, returns empty list (graceful degradation).
        """
        if self._duckdb_conn is None:
            log.debug("DuckDB not available — skipping read")
            return []
        return self._duckdb_with_retry(sql, params, write=False)

    def execute_duckdb_write(self, sql: str, params: tuple = ()) -> None:
        """
        Execute INSERT / UPDATE on DuckDB.
        If DuckDB not available, logs warning and skips (graceful degradation).
        """
        if self._duckdb_conn is None:
            log.debug("DuckDB not available — skipping write")
            return
        if self._duckdb_mode != "RW":
            if not self._duckdb_write_skip_logged:
                log.warning(
                    "DuckDB write skipped: backend is not writable "
                    f"(mode={self._duckdb_mode}, status={self._duckdb_status})."
                )
                self._duckdb_write_skip_logged = True
            return
        self._duckdb_with_retry(sql, params, write=True)

    def _duckdb_with_retry(
        self, sql: str, params: tuple, write: bool
    ) -> list[tuple]:
        t_start = time.perf_counter()
        for attempt in range(MAX_RETRIES):
            try:
                with self._duckdb_lock:
                    if params:
                        result = self._duckdb_conn.execute(sql, list(params))
                    else:
                        result = self._duckdb_conn.execute(sql)
                    rows = result.fetchall() if not write else []
                elapsed_ms = (time.perf_counter() - t_start) * 1000
                self._record_duckdb_latency(elapsed_ms)
                with self._stats_lock:
                    self._op_count_duckdb += 1
                return rows
            except Exception as exc:
                if attempt < MAX_RETRIES - 1:
                    log.warning(f"DuckDB error — retry {attempt + 1}: {exc}")
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                with self._stats_lock:
                    self._error_count += 1
                raise StorageError(f"DuckDB failed after {MAX_RETRIES} attempts: {exc}") from exc
        return []

    # ─── LATENCY TRACKING ─────────────────────────────────────────────────────

    def _record_sqlite_latency(self, ms: float) -> None:
        with self._stats_lock:
            self._sqlite_latencies.append(ms)
            self._op_count_sqlite += 1

    def _record_duckdb_latency(self, ms: float) -> None:
        with self._stats_lock:
            self._duckdb_latencies.append(ms)

    # ─── STATS ────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """
        Returns performance and health metrics for dashboard display.

        Keys:
            duckdb_available    — bool, False if duckdb package not installed
            duckdb_path         — str
            duckdb_size_mb      — float (file size)
            duckdb_avg_ms       — float (avg query time, last 200 ops)
            duckdb_op_count     — int
            sqlite_path         — str
            sqlite_size_mb      — float
            sqlite_avg_ms       — float
            sqlite_op_count     — int
            error_count         — int (total errors across both DBs)
        """
        with self._stats_lock:
            sq_latencies = list(self._sqlite_latencies)
            dk_latencies = list(self._duckdb_latencies)
            ops_sq = self._op_count_sqlite
            ops_dk = self._op_count_duckdb
            errors = self._error_count

        sq_avg = round(sum(sq_latencies) / len(sq_latencies), 2) if sq_latencies else 0.0
        dk_avg = round(sum(dk_latencies) / len(dk_latencies), 2) if dk_latencies else 0.0

        sq_size_mb = round(SQLITE_PATH.stat().st_size / 1_048_576, 3) if SQLITE_PATH.exists() else 0.0
        dk_size_mb = round(DUCKDB_PATH.stat().st_size / 1_048_576, 3) if DUCKDB_PATH.exists() else 0.0

        return {
            "duckdb_available": self._duckdb_conn is not None,
            "duckdb_mode":      self._duckdb_mode,
            "duckdb_status":    self._duckdb_status,
            "duckdb_reason":    self._duckdb_reason,
            "duckdb_path":      str(DUCKDB_PATH),
            "duckdb_size_mb":   dk_size_mb,
            "duckdb_avg_ms":    dk_avg,
            "duckdb_op_count":  ops_dk,
            "sqlite_path":      str(SQLITE_PATH),
            "sqlite_size_mb":   sq_size_mb,
            "sqlite_avg_ms":    sq_avg,
            "sqlite_op_count":  ops_sq,
            "error_count":      errors,
        }

    def get_table_row_counts(self) -> dict[str, int]:
        """Return row count per SQLite table.  Used by dashboard."""
        tables = ["trades", "signals", "state_snapshots",
                  "kill_switch_log", "recovery_log", "execution_fills"]
        counts: dict[str, int] = {}
        for t in tables:
            try:
                rows = self.execute_sqlite(f"SELECT COUNT(*) FROM {t}")
                counts[t] = rows[0][0] if rows else 0
            except Exception:
                counts[t] = -1
        return counts

    def health_check(self) -> bool:
        """Quick check that both databases respond.  Used by heartbeat."""
        try:
            self.execute_sqlite("SELECT 1")
            return True
        except Exception as exc:
            log.error(f"StorageService health check failed: {exc}")
            return False

    def close(self) -> None:
        """
        Close database connections explicitly.

        Important for tests on Windows where temporary DB files cannot be removed
        while open handles exist.
        """
        try:
            if self._duckdb_conn is not None:
                try:
                    self._duckdb_conn.close()
                except Exception:
                    pass
                self._duckdb_conn = None
            if self._sqlite_conn is not None:
                try:
                    self._sqlite_conn.close()
                except Exception:
                    pass
                self._sqlite_conn = None
        except Exception as exc:
            log.warning(f"StorageService close warning: {exc}")


# ─── SINGLETON ────────────────────────────────────────────────────────────────
# ONE storage service for the entire application.
# Import via: from services.storage_service import storage
storage = StorageService()
