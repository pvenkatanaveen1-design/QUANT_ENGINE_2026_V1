"""
Trade journal — append-only SQLite history of execution outcomes (Phase 13).

Watches Redis `execution:*` snapshots written by `execution/broker_bridge.py`.
When `execution:last_update` / `execution:last_status` changes to a new fingerprint,
we insert one row (skips duplicates and skips IDLE bridge heartbeats).

No analytics here — storage + dashboard hooks only.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.bus import get_value, set_value
from core.config import load_config
from core.logger import get_logger

log = get_logger()

# Database lives next to this file so paths stay simple for beginners.
_JOURNAL_DIR = Path(__file__).resolve().parent
JOURNAL_DB_PATH = _JOURNAL_DIR / "trade_journal.db"

# Poll interval for `run_trade_logger()` daemon (seconds).
TRADE_LOGGER_INTERVAL_SECONDS = 2.0

# Status values we persist (skip IDLE — bridge warmup only).
_LOGGABLE_STATUSES = frozenset({"FILLED", "REJECTED", "BLOCKED", "APPROVED"})


def journal_db_path() -> Path:
    """Absolute path to `trade_journal.db` (for tests / dashboard)."""
    return JOURNAL_DB_PATH


def initialize_database() -> Path:
    """
    Ensure the journal directory exists and the SQLite file can be opened.

    Returns:
        Path to `trade_journal.db`
    """
    _JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(JOURNAL_DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.commit()
    finally:
        conn.close()
    log.info("trade_logger | database ready | path={}", JOURNAL_DB_PATH)
    return JOURNAL_DB_PATH


def create_trades_table(conn: sqlite3.Connection) -> None:
    """Create the `trades` table if it does not exist (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            symbol TEXT,
            side TEXT,
            volume REAL,
            entry_price REAL,
            stop_loss REAL,
            take_profit REAL,
            execution_status TEXT NOT NULL,
            mt5_ticket INTEGER,
            router_decision TEXT,
            session TEXT,
            system_mode TEXT,
            execution_reason TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades (timestamp DESC)
        """
    )
    conn.commit()
    log.info("trade_logger | trades table verified")


def _parse_float(val: object | None) -> float | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_int(val: object | None) -> int | None:
    if val is None:
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def build_trade_record() -> dict[str, Any]:
    """
    Snapshot Redis + config into one row dict (keys match `trades` columns).

    `entry_price` / `stop_loss` / `take_profit` are not on execution:* yet — left None.
    """
    cfg = load_config()
    mode = str(cfg.get("SYSTEM_MODE") or "TEST").strip().upper()

    ex_ts = get_value("execution:last_update")
    ts = _parse_float(ex_ts)
    if ts is None:
        ts = datetime.now(timezone.utc).timestamp()

    sym = get_value("execution:last_symbol")
    side = get_value("execution:last_side")
    vol = _parse_float(get_value("execution:last_volume"))
    st = get_value("execution:last_status")
    status = str(st or "").strip().upper() or "UNKNOWN"

    ticket = _parse_int(get_value("execution:last_ticket"))
    reason = get_value("execution:last_reason")
    reason_s = str(reason) if reason is not None else None

    router = get_value("router:last_decision")
    router_s = str(router) if router is not None else None

    sess = get_value("clock:session")
    sess_s = str(sess) if sess is not None else None

    return {
        "timestamp": float(ts),
        "symbol": str(sym).upper() if sym is not None else None,
        "side": str(side).upper() if side is not None else None,
        "volume": vol,
        "entry_price": None,
        "stop_loss": None,
        "take_profit": None,
        "execution_status": status,
        "mt5_ticket": ticket,
        "router_decision": router_s,
        "session": sess_s,
        "system_mode": mode,
        "execution_reason": reason_s,
    }


def insert_trade_record(row: dict[str, Any]) -> int:
    """
    Insert one trade row. Returns new primary key `id`.

    Caller should avoid duplicate business keys — the daemon uses Redis fingerprints.
    """
    initialize_database()
    conn = sqlite3.connect(str(JOURNAL_DB_PATH))
    try:
        create_trades_table(conn)
        cur = conn.execute(
            """
            INSERT INTO trades (
                timestamp, symbol, side, volume, entry_price, stop_loss, take_profit,
                execution_status, mt5_ticket, router_decision, session, system_mode,
                execution_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["timestamp"],
                row.get("symbol"),
                row.get("side"),
                row.get("volume"),
                row.get("entry_price"),
                row.get("stop_loss"),
                row.get("take_profit"),
                row["execution_status"],
                row.get("mt5_ticket"),
                row.get("router_decision"),
                row.get("session"),
                row.get("system_mode"),
                row.get("execution_reason"),
            ),
        )
        conn.commit()
        new_id = int(cur.lastrowid)
        log.info(
            "trade_logger | insert | id={} status={} symbol={}",
            new_id,
            row.get("execution_status"),
            row.get("symbol"),
        )
        return new_id
    finally:
        conn.close()


def fetch_recent_trades(limit: int = 50) -> list[dict[str, Any]]:
    """Return newest rows first as plain dicts (dashboard + tests)."""
    if not JOURNAL_DB_PATH.exists():
        return []
    lim = max(1, min(int(limit), 500))
    conn = sqlite3.connect(str(JOURNAL_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        create_trades_table(conn)
        cur = conn.execute(
            """
            SELECT id, timestamp, symbol, side, volume, entry_price, stop_loss, take_profit,
                   execution_status, mt5_ticket, router_decision, session, system_mode,
                   execution_reason
            FROM trades
            ORDER BY id DESC
            LIMIT ?
            """,
            (lim,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _count_trades() -> int:
    if not JOURNAL_DB_PATH.exists():
        return 0
    conn = sqlite3.connect(str(JOURNAL_DB_PATH))
    try:
        create_trades_table(conn)
        cur = conn.execute("SELECT COUNT(*) FROM trades")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def publish_journal_status(
    *,
    last_trade_id: int | None,
    last_row: dict[str, Any] | None,
    total_trades: int,
) -> None:
    """Mirror journal summary to Redis for lightweight dashboards."""
    now = time.time()
    try:
        if last_row is not None:
            set_value("journal:last_trade", dict(last_row))
        else:
            set_value("journal:last_trade", None)

        last_st = None
        if last_row is not None:
            last_st = last_row.get("execution_status")
        set_value("journal:last_status", last_st)

        set_value("journal:total_trades", int(total_trades))
        set_value("journal:last_update", now)
        log.info(
            "trade_logger | publish_journal_status | total={} last_id={}",
            total_trades,
            last_trade_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("trade_logger | publish_journal_status failed | {}", exc)


# Process-local deduplication: last Redis execution fingerprint we persisted.
_last_logged_fingerprint: tuple[Any, str] | None = None


def _execution_fingerprint() -> tuple[Any, str] | None:
    """(execution:last_update, execution:last_status) or None if nothing to log."""
    ts = get_value("execution:last_update")
    st = get_value("execution:last_status")
    if ts is None and st is None:
        return None
    return (ts, str(st or "").strip().upper())


def tick_trade_logger() -> bool:
    """
    One poll: if execution snapshot is new and loggable, insert + publish.

    Returns:
        True if a new row was inserted.
    """
    global _last_logged_fingerprint

    fp = _execution_fingerprint()
    if fp is None:
        return False

    status = fp[1]
    if status not in _LOGGABLE_STATUSES:
        # Advance cursor so IDLE / unknown noise does not re-trigger forever.
        _last_logged_fingerprint = fp
        return False

    if fp == _last_logged_fingerprint:
        return False

    try:
        row = build_trade_record()
        # Normalize status from built row (defensive).
        row["execution_status"] = status
        new_id = insert_trade_record(row)
        total = _count_trades()
        row_with_id = dict(row)
        row_with_id["id"] = new_id
        publish_journal_status(last_trade_id=new_id, last_row=row_with_id, total_trades=total)
        _last_logged_fingerprint = fp
        return True
    except Exception as exc:  # noqa: BLE001
        log.exception("trade_logger | tick failed | {}", exc)
        return False


def reset_dedup_cursor() -> None:
    """Tests only: forget last fingerprint so the next snapshot logs again."""
    global _last_logged_fingerprint
    _last_logged_fingerprint = None


def sync_dedup_from_redis() -> None:
    """
    Align local fingerprint with current Redis so a process restart does not re-insert
    the same execution snapshot.
    """
    global _last_logged_fingerprint
    fp = _execution_fingerprint()
    if fp is not None and fp[1] in _LOGGABLE_STATUSES:
        _last_logged_fingerprint = fp
        log.info("trade_logger | dedup synced from Redis | fp={}", fp)


def run_trade_logger() -> None:
    """
    Daemon loop (started from `run.py`): watch Redis execution:* and append rows.

    Safe alongside `broker_bridge` — only reacts to new fingerprints.
    """
    log.info(
        "trade_logger | daemon start | interval_s={} | db={}",
        TRADE_LOGGER_INTERVAL_SECONDS,
        JOURNAL_DB_PATH,
    )
    initialize_database()
    conn = sqlite3.connect(str(JOURNAL_DB_PATH))
    try:
        create_trades_table(conn)
    finally:
        conn.close()

    sync_dedup_from_redis()
    total = _count_trades()
    rows = fetch_recent_trades(1)
    if rows:
        top = rows[0]
        publish_journal_status(
            last_trade_id=int(top["id"]),
            last_row=dict(top),
            total_trades=total,
        )
    else:
        publish_journal_status(last_trade_id=None, last_row=None, total_trades=total)

    while True:
        try:
            tick_trade_logger()
        except Exception:  # noqa: BLE001
            log.exception("trade_logger | loop tick error")
        time.sleep(TRADE_LOGGER_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_trade_logger()
