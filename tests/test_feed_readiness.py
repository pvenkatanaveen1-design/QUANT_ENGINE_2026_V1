"""Feed readiness helper coverage — avoids accidental regressions in startup UX."""

from __future__ import annotations

from unittest.mock import patch

from core.config import describe_mt5_feed_readiness


def test_feed_readiness_ok_when_symbols_present():
    cfg = {
        "MT5_SYMBOLS": ["XAUUSD"],
        "MT5_SERVER": "Broker-Demo",
        "MT5_LOGIN": 12345,
        "SYSTEM_MODE": "TEST",
    }
    with patch("core.config.load_config", return_value=cfg):
        report = describe_mt5_feed_readiness()
    assert report["ok"] is True
    assert report["symbols"] == ["XAUUSD"]


def test_feed_readiness_issues_when_symbols_missing():
    cfg = {
        "MT5_SYMBOLS": [],
        "MT5_SERVER": "",
        "MT5_LOGIN": None,
        "SYSTEM_MODE": "TEST",
    }
    with patch("core.config.load_config", return_value=cfg):
        report = describe_mt5_feed_readiness()
    assert report["ok"] is False
    assert any("MT5_SYMBOLS" in msg for msg in report["issues"])


def test_feed_strict_flag_reads_env():
    cfg = {
        "MT5_SYMBOLS": [],
        "MT5_SERVER": "",
        "MT5_LOGIN": None,
        "SYSTEM_MODE": "TEST",
    }
    with patch("core.config.load_config", return_value=cfg):
        with patch.dict("os.environ", {"QUANT_STRICT_MT5_CONFIG": "1"}):
            report = describe_mt5_feed_readiness()
    assert report["strict_exit_requested"] is True
