"""
tests/test_storage_service.py — Tests for services/storage_service.py

Run: pytest tests/test_storage_service.py -v
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture
def temp_storage():
    """Create a temporary StorageService with test databases."""
    import tempfile
    import os
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


class TestSQLite:
    def test_sqlite_write_and_read(self, temp_storage):
        """Write a row and read it back."""
        temp_storage.execute_sqlite_write(
            "INSERT INTO kill_switch_log (activated_at, reason, triggered_by, active) "
            "VALUES (?,?,?,?)",
            ("2026-01-01T00:00:00", "test reason", "pytest", 1),
        )
        rows = temp_storage.execute_sqlite(
            "SELECT reason FROM kill_switch_log WHERE triggered_by = 'pytest'"
        )
        assert len(rows) == 1
        assert rows[0]["reason"] == "test reason"

    def test_sqlite_multiple_writes(self, temp_storage):
        for i in range(10):
            temp_storage.execute_sqlite_write(
                "INSERT INTO kill_switch_log (activated_at, reason, triggered_by, active) "
                "VALUES (?,?,?,?)",
                (f"2026-01-0{i+1}T00:00:00", f"reason_{i}", "batch_test", 0),
            )
        rows = temp_storage.execute_sqlite(
            "SELECT COUNT(*) FROM kill_switch_log WHERE triggered_by = 'batch_test'"
        )
        assert rows[0][0] == 10

    def test_get_stats_returns_dict(self, temp_storage):
        stats = temp_storage.get_stats()
        assert isinstance(stats, dict)
        assert "sqlite_size_mb" in stats
        assert "sqlite_avg_ms" in stats
        assert "error_count" in stats

    def test_health_check(self, temp_storage):
        assert temp_storage.health_check() is True


class TestTables:
    def test_all_tables_created(self, temp_storage):
        """Verify all required SQLite tables exist."""
        tables = ["trades", "signals", "state_snapshots",
                  "kill_switch_log", "recovery_log", "execution_fills"]
        for table in tables:
            rows = temp_storage.execute_sqlite(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            assert rows, f"Table '{table}' not found"

    def test_table_row_counts(self, temp_storage):
        counts = temp_storage.get_table_row_counts()
        assert isinstance(counts, dict)
        assert "trades" in counts
