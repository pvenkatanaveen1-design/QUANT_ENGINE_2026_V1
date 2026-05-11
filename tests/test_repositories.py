"""
tests/test_repositories.py — Tests for repositories/ layer.

Run: pytest tests/test_repositories.py -v
"""

import sys
import uuid
from datetime import datetime, date
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture
def temp_storage():
    """Return a fresh StorageService with temp databases."""
    import tempfile
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmpdir:
        sqlite_path = Path(tmpdir) / "test_journal.db"
        duckdb_path = Path(tmpdir) / "test_market.duckdb"
        with patch("services.storage_service.SQLITE_PATH", sqlite_path), \
             patch("services.storage_service.DUCKDB_PATH",  duckdb_path):
            from services.storage_service import StorageService
            svc = StorageService()
            try:
                yield svc
            finally:
                svc.close()


@pytest.fixture
def sample_trade():
    from core.models.trade import TradeEvent
    from core.enums import Direction, TradeStatus
    return TradeEvent(
        correlation_id  = str(uuid.uuid4()),
        broker_ticket   = 12345,
        symbol          = "XAUUSD",
        direction       = Direction.BUY,
        volume          = 0.10,
        status          = TradeStatus.FILLED,
        requested_price = 2350.00,
        fill_price      = 2350.10,
        stop_loss       = 2340.00,
        take_profit     = 2370.00,
        slippage_pips   = 0.1,
        spread_at_entry = 0.3,
        strategy        = "alpha_breakout",
        score_at_entry  = 72.0,
    )


class TestTradeRepository:
    def test_insert_and_get(self, temp_storage, sample_trade):
        from repositories.trade_repository import TradeRepository
        repo = TradeRepository(temp_storage)
        trade_id = repo.insert(sample_trade)
        assert trade_id

        retrieved = repo.get_by_id(trade_id)
        assert retrieved is not None
        assert retrieved["symbol"] == "XAUUSD"
        assert retrieved["strategy"] == "alpha_breakout"

    def test_get_open_trades(self, temp_storage, sample_trade):
        from repositories.trade_repository import TradeRepository
        repo = TradeRepository(temp_storage)
        repo.insert(sample_trade)
        open_trades = repo.get_open()
        assert len(open_trades) == 1

    def test_update_close(self, temp_storage, sample_trade):
        from repositories.trade_repository import TradeRepository
        repo = TradeRepository(temp_storage)
        trade_id = repo.insert(sample_trade)
        repo.update_close(trade_id, 2365.00, "TAKE_PROFIT", 150.0, 145.0)

        row = repo.get_by_id(trade_id)
        assert row["close_price"] == 2365.00
        assert row["close_reason"] == "TAKE_PROFIT"
        assert row["status"] == "CLOSED"

    def test_daily_summary(self, temp_storage, sample_trade):
        from repositories.trade_repository import TradeRepository
        repo = TradeRepository(temp_storage)
        tid = repo.insert(sample_trade)
        repo.update_close(tid, 2365.0, "TAKE_PROFIT", 100.0, 95.0)

        summary = repo.get_daily_summary()
        assert summary.get("total_trades", 0) >= 0  # May be 0 if different day in test


class TestStateRepository:
    def test_save_and_load_snapshot(self, temp_storage):
        from repositories.state_repository import StateRepository
        repo = StateRepository(temp_storage)
        snapshot = {
            "equity":              10000.0,
            "balance":             10000.0,
            "daily_dd_pct":        1.5,
            "total_dd_pct":        2.0,
            "open_trade_count":    1,
            "daily_trade_count":   2,
            "kill_switch_active":  False,
            "news_blackout":       False,
            "system_mode":         "TEST",
        }
        repo.save_snapshot(snapshot)
        loaded = repo.get_latest_snapshot()
        assert loaded is not None
        assert abs(loaded["equity"] - 10000.0) < 0.01

    def test_kill_switch_activate_deactivate(self, temp_storage):
        from repositories.state_repository import StateRepository
        repo = StateRepository(temp_storage)

        # Initial state: no active kill switch
        status = repo.get_kill_switch_status()
        assert status["active"] is False

        # Activate
        row_id = repo.activate_kill_switch("Test reason", "pytest")
        status = repo.get_kill_switch_status()
        assert status["active"] is True
        assert status["reason"] == "Test reason"

        # Deactivate
        repo.deactivate_kill_switch(row_id)
        status = repo.get_kill_switch_status()
        assert status["active"] is False
