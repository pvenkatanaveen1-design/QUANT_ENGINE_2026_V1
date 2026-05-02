from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from core.clock import utc_now
from core.paths import project_root

_DB_CACHE: sqlite3.Connection | None = None


def db_path() -> Path:
    return project_root() / "logs" / "trade_journal.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def get_connection() -> sqlite3.Connection:
    global _DB_CACHE  # noqa: PLW0603
    if _DB_CACHE is None:
        _DB_CACHE = connect()
        init_schema(_DB_CACHE)
    return _DB_CACHE


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS journal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            category TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_journal_cat_ts ON journal_events (category, ts_utc);
        """
    )
    conn.commit()


def log_event(category: str, payload: dict[str, Any]) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO journal_events (ts_utc, category, payload) VALUES (?, ?, ?)",
        (
            utc_now().isoformat(),
            category,
            json.dumps(payload, default=str),
        ),
    )
    conn.commit()


def log_dataclass(category: str, obj: Any) -> None:
    payload = asdict(obj) if hasattr(obj, "__dataclass_fields__") else {"repr": repr(obj)}
    log_event(category, payload)
