"""
tests/test_backtester.py — Tests for systems/research/backtester.py

Run: pytest tests/test_backtester.py -v
"""

import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


def _generate_synthetic_candles(n=500, trend="up"):
    """Generate synthetic OHLCV candle data for backtesting."""
    from datetime import datetime, timedelta
    import random

    candles = []
    price = 2300.0
    start = datetime(2025, 1, 1)

    for i in range(n):
        if trend == "up":
            change = random.uniform(-3, 5)
        elif trend == "down":
            change = random.uniform(-5, 3)
        else:
            change = random.uniform(-4, 4)

        open_p  = price
        close_p = price + change
        high_p  = max(open_p, close_p) + random.uniform(0.5, 2.0)
        low_p   = min(open_p, close_p) - random.uniform(0.5, 2.0)

        candles.append({
            "time":   start + timedelta(hours=i),
            "open":   round(open_p, 2),
            "high":   round(high_p, 2),
            "low":    round(low_p, 2),
            "close":  round(close_p, 2),
            "volume": 1,
        })
        price = close_p

    return candles


class TestBacktesterBasic:
    def test_run_returns_result(self):
        from systems.research.backtester import Backtester
        from strategies.alpha_breakout import AlphaBreakout

        candles = _generate_synthetic_candles(300)
        bt      = Backtester()
        result  = bt.run(AlphaBreakout(), candle_data=candles)
        assert result is not None
        assert result.strategy == "alpha_breakout"

    def test_result_has_metrics(self):
        from systems.research.backtester import Backtester
        from strategies.alpha_breakout import AlphaBreakout

        candles = _generate_synthetic_candles(300)
        bt      = Backtester()
        result  = bt.run(AlphaBreakout(), candle_data=candles)

        assert isinstance(result.total_trades, int)
        assert isinstance(result.win_rate, float)
        assert isinstance(result.profit_factor, float)
        assert isinstance(result.sharpe_ratio, float)
        assert isinstance(result.max_drawdown_pct, float)

    def test_equity_curve_non_empty(self):
        from systems.research.backtester import Backtester
        from strategies.alpha_breakout import AlphaBreakout

        candles = _generate_synthetic_candles(300)
        bt      = Backtester()
        result  = bt.run(AlphaBreakout(), candle_data=candles)

        # Even if no trades, should have at least the starting equity
        assert len(result.equity_curve) >= 1

    def test_empty_data_returns_graceful_result(self):
        from systems.research.backtester import Backtester
        from strategies.alpha_breakout import AlphaBreakout

        bt     = Backtester()
        result = bt.run(AlphaBreakout(), candle_data=[])
        assert result.total_trades == 0

    def test_no_negative_lot_size(self):
        from systems.research.backtester import Backtester
        from strategies.alpha_breakout import AlphaBreakout

        candles = _generate_synthetic_candles(300, trend="up")
        bt      = Backtester()
        result  = bt.run(AlphaBreakout(), candle_data=candles)
        for trade in result.trades:
            assert trade["lot_size"] >= 0.01


class TestMonteCarlo:
    def test_basic_run(self):
        from systems.research.monte_carlo import MonteCarlo
        mc = MonteCarlo(n_simulations=100)
        pnls = [50, -30, 80, -40, 60, -50, 120, -30, 40, -20] * 20
        result = mc.run(pnls, initial_equity=10000.0)
        assert result.n_simulations == 100
        assert result.n_trades == len(pnls)
        assert 0 <= result.risk_of_ruin_pct <= 100
        assert result.max_dd_p50 >= 0

    def test_ruin_with_terrible_trades(self):
        from systems.research.monte_carlo import MonteCarlo
        mc = MonteCarlo(n_simulations=500, ruin_threshold=0.50)
        # All losing trades — very high risk of ruin
        pnls = [-200] * 100
        result = mc.run(pnls, initial_equity=10000.0)
        assert result.risk_of_ruin_pct > 50  # Should almost always ruin

    def test_percentiles_ordered(self):
        from systems.research.monte_carlo import MonteCarlo
        mc = MonteCarlo(n_simulations=200)
        pnls = [50, -30, 80, -40, 60] * 40
        result = mc.run(pnls)
        # P5 max DD should be ≤ P50 ≤ P95
        assert result.max_dd_p5 <= result.max_dd_p50 <= result.max_dd_p95
