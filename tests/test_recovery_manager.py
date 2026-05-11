"""
tests/test_recovery_manager.py — Tests for core/recovery_manager.py

Run: pytest tests/test_recovery_manager.py -v
"""

import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture
def mock_repos():
    """Mock repositories to avoid needing a real DB."""
    state_repo = MagicMock()
    trade_repo = MagicMock()

    # Default: no kill switch active, no previous snapshot
    state_repo.get_kill_switch_status.return_value = {
        "active": False, "reason": "", "activated_at": "", "row_id": -1
    }
    state_repo.get_latest_snapshot.return_value = {
        "equity": 9800.0, "timestamp": "2026-01-01T10:00:00"
    }
    trade_repo.get_daily_trades.return_value = []
    trade_repo.get_open.return_value = []

    return state_repo, trade_repo


class TestRecoveryManagerSteps:
    def test_recovery_runs_without_error(self, tmp_path, mock_repos):
        """Full recovery run should not raise."""
        state_repo, trade_repo = mock_repos

        with patch("core.recovery_manager._RESTART_LOG", tmp_path / "restart.json"), \
             patch("core.recovery_manager._RECOVERY_LOG", tmp_path / "recovery.json"), \
             patch("core.recovery_manager.is_live", return_value=False):

            from core.recovery_manager import RecoveryManager
            rm = RecoveryManager.__new__(RecoveryManager)
            rm._state_repo = state_repo
            rm._trade_repo = trade_repo

            from core.recovery_manager import RecoveryReport
            rm._report = RecoveryReport()

            # Run individual steps
            rm._step1_record_restart()
            rm._step2_restore_state_snapshot()
            rm._step3_restore_kill_switch()
            rm._step4_rebuild_daily_dd()
            rm._step5_reconcile_mt5()

            assert not rm._report.kill_switch_was_active
            assert rm._report.last_equity == 9800.0

    def test_kill_switch_restoration(self, tmp_path, mock_repos):
        """If kill switch was active before crash, it must be re-activated."""
        state_repo, trade_repo = mock_repos
        state_repo.get_kill_switch_status.return_value = {
            "active": True,
            "reason": "DD limit breach",
            "activated_at": "2026-01-01T09:00:00",
            "row_id": 1,
        }

        with patch("core.recovery_manager._RESTART_LOG", tmp_path / "restart.json"), \
             patch("core.recovery_manager._RECOVERY_LOG", tmp_path / "recovery.json"), \
             patch("core.recovery_manager.is_live", return_value=False):

            # Mock state.activate_kill_switch so we don't need real state store
            with patch("core.recovery_manager.state") as mock_state:
                mock_state.activate_kill_switch = MagicMock()
                mock_state.get_equity.return_value = 9800.0

                from core.recovery_manager import RecoveryManager, RecoveryReport
                rm = RecoveryManager.__new__(RecoveryManager)
                rm._state_repo = state_repo
                rm._trade_repo = trade_repo
                rm._report     = RecoveryReport()

                rm._step3_restore_kill_switch()

                assert rm._report.kill_switch_was_active is True
                assert "DD limit breach" in rm._report.kill_switch_reason
                mock_state.activate_kill_switch.assert_called_once()

    def test_restart_log_written(self, tmp_path, mock_repos):
        """Restart should be recorded in restart_log.json."""
        state_repo, trade_repo = mock_repos
        restart_log = tmp_path / "restart_log.json"

        with patch("core.recovery_manager._RESTART_LOG", restart_log), \
             patch("core.recovery_manager._RECOVERY_LOG", tmp_path / "recovery.json"), \
             patch("core.recovery_manager.is_live", return_value=False):

            from core.recovery_manager import RecoveryManager, RecoveryReport
            rm = RecoveryManager.__new__(RecoveryManager)
            rm._state_repo = state_repo
            rm._trade_repo = trade_repo
            rm._report     = RecoveryReport()

            rm._step1_record_restart()

            assert restart_log.exists()
            data = json.loads(restart_log.read_text())
            assert len(data) >= 1
            assert "timestamp" in data[-1]

    def test_test_mode_skips_mt5(self, tmp_path, mock_repos):
        """In TEST mode, MT5 reconciliation should be skipped."""
        state_repo, trade_repo = mock_repos

        with patch("core.recovery_manager.is_live", return_value=False):
            from core.recovery_manager import RecoveryManager, RecoveryReport
            rm = RecoveryManager.__new__(RecoveryManager)
            rm._state_repo = state_repo
            rm._trade_repo = trade_repo
            rm._report     = RecoveryReport()

            rm._step5_reconcile_mt5()

            assert "SIMULATED" in rm._report.reconciliation_status
