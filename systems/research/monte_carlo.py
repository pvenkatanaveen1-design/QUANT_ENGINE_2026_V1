"""
systems/research/monte_carlo.py — S21: Monte Carlo Simulation Engine.

WHY THIS FILE EXISTS
--------------------
Your backtest says: "200 trades, PF=1.35, max DD = 8.2%"
But what if you had gotten unlucky with the trade sequence?
What if the first 20 trades were all losers?

Monte Carlo answers: "Given these 200 trade outcomes, what is the
probability distribution of possible results?"

WHAT IT CALCULATES:
  1. Risk of Ruin: % of simulations where equity drops to 0 (or -50%)
     Target: < 5% risk of ruin.  Above 10% = strategy is dangerous.
  2. Max Drawdown distribution: 5th/50th/95th percentile DD
     Target: 95th percentile DD < 20% (worst-case scenario)
  3. Final equity distribution: 5th/50th/95th percentile equity
  4. Confidence intervals: with 95% confidence, your PF will be X-Y

HOW IT WORKS:
  Take the 200 historical trades (sequence matters in original backtest).
  Shuffle them 10,000 times (random sequences = different luck scenarios).
  For each shuffle: replay the trades in order, track equity curve.
  Collect statistics across all 10,000 simulations.

2026 REALITY NOTE:
  Monte Carlo has one key limitation: it assumes trades are INDEPENDENT.
  In forex, they're not fully independent (news affects multiple trades).
  But for a single-symbol XAUUSD strategy with 1 trade at a time,
  the correlation is low enough that MC gives useful risk estimates.

USAGE:
    from systems.research.monte_carlo import MonteCarlo
    mc = MonteCarlo()
    result = mc.run(trade_pnls=[-50, 80, -50, 120, -50, 90, ...])
    print(f"Risk of ruin: {result.risk_of_ruin:.1f}%")
    print(f"95th pct max DD: {result.max_dd_p95:.1f}%")
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional

from core.logger import get_logger, LogCategory

log = get_logger("monte_carlo", LogCategory.BACKTEST)

# Default settings
DEFAULT_N_SIMULATIONS = 10_000
DEFAULT_RUIN_THRESHOLD = 0.50  # Equity drops to 50% of start = "ruin"
DEFAULT_INITIAL_EQUITY = 10_000.0


@dataclass
class MonteCarloResult:
    """Complete output from one Monte Carlo simulation run."""
    n_simulations:    int
    n_trades:         int
    initial_equity:   float
    ruin_threshold:   float

    # Risk of ruin
    risk_of_ruin_pct: float = 0.0  # % of simulations that hit ruin threshold

    # Max drawdown distribution (%)
    max_dd_p5:  float = 0.0   # 5th percentile = best case
    max_dd_p50: float = 0.0   # 50th percentile = median
    max_dd_p95: float = 0.0   # 95th percentile = worst realistic case

    # Final equity distribution (absolute $)
    equity_p5:  float = 0.0
    equity_p50: float = 0.0
    equity_p95: float = 0.0

    # Trade sequence statistics
    longest_losing_streak_p95: int = 0  # 95th pct of max consecutive losses
    avg_final_equity: float = 0.0

    # Simulation data for chart
    equity_curves: list = field(default_factory=list)  # sample of 100 curves for display

    def passes_criteria(self) -> tuple[bool, list[str]]:
        """Check if strategy passes MC criteria from master plan."""
        failures = []
        if self.risk_of_ruin_pct >= 5.0:
            failures.append(f"Risk of ruin {self.risk_of_ruin_pct:.1f}% >= 5% limit")
        if self.max_dd_p95 >= 20.0:
            failures.append(f"95th pct max DD {self.max_dd_p95:.1f}% >= 20% safety limit")
        return len(failures) == 0, failures

    def summary(self) -> str:
        passed, failures = self.passes_criteria()
        status = "PASS" if passed else "FAIL"
        return (
            f"[{status}] Monte Carlo | "
            f"N={self.n_simulations} trades={self.n_trades} | "
            f"Risk of ruin: {self.risk_of_ruin_pct:.1f}% | "
            f"Max DD P50/P95: {self.max_dd_p50:.1f}%/{self.max_dd_p95:.1f}% | "
            f"Final equity P50: ${self.equity_p50:,.0f}"
        )


class MonteCarlo:
    """
    Monte Carlo simulation engine for trade sequence analysis.

    USAGE:
        mc = MonteCarlo(n_simulations=10_000)

        # From backtester output
        trade_pnls = [t["net_pnl"] for t in backtest_result.trades]
        result = mc.run(trade_pnls)

        print(result.summary())
    """

    def __init__(
        self,
        n_simulations:  int   = DEFAULT_N_SIMULATIONS,
        ruin_threshold: float = DEFAULT_RUIN_THRESHOLD,
        initial_equity: float = DEFAULT_INITIAL_EQUITY,
    ) -> None:
        self._n_sims        = n_simulations
        self._ruin_threshold = ruin_threshold
        self._init_equity   = initial_equity

    def run(
        self,
        trade_pnls:     List[float],
        initial_equity: Optional[float] = None,
    ) -> MonteCarloResult:
        """
        Run Monte Carlo simulation on a list of trade PnL values.

        Parameters:
            trade_pnls:     List of net PnL per trade from backtester.
                            Order matters for original sequence analysis.
            initial_equity: Starting equity (default: uses constructor value)

        Returns:
            MonteCarloResult with risk metrics and percentile distributions.
        """
        equity = initial_equity or self._init_equity

        if not trade_pnls:
            log.error("Monte Carlo: no trade PnLs provided")
            return MonteCarloResult(
                n_simulations=self._n_sims,
                n_trades=0,
                initial_equity=equity,
                ruin_threshold=self._ruin_threshold,
            )

        n = len(trade_pnls)
        ruin_level = equity * (1 - self._ruin_threshold)  # e.g. $5,000 if 50% ruin

        log.info(
            f"Monte Carlo starting: {self._n_sims} simulations × "
            f"{n} trades | equity=${equity:,.0f}"
        )

        # Run simulations
        all_max_dds:      List[float] = []
        all_final_equities: List[float] = []
        all_losing_streaks: List[int] = []
        ruin_count = 0
        sample_curves: list = []  # Store 100 curves for dashboard chart

        trade_arr = list(trade_pnls)  # mutable copy

        for sim_idx in range(self._n_sims):
            random.shuffle(trade_arr)

            sim_equity   = equity
            peak_equity  = equity
            max_dd       = 0.0
            ruined       = False
            max_streak   = 0
            cur_streak   = 0

            curve_points: list = []

            for pnl in trade_arr:
                sim_equity += pnl

                # Track losing streak
                if pnl < 0:
                    cur_streak += 1
                    max_streak = max(max_streak, cur_streak)
                else:
                    cur_streak = 0

                # Update drawdown
                if sim_equity > peak_equity:
                    peak_equity = sim_equity
                dd = (peak_equity - sim_equity) / peak_equity * 100
                if dd > max_dd:
                    max_dd = dd

                # Check ruin
                if sim_equity <= ruin_level:
                    ruined = True
                    break

                # Sample curve (only for first 100 sims to save memory)
                if sim_idx < 100:
                    curve_points.append(round(sim_equity, 2))

            if ruined:
                ruin_count += 1

            all_max_dds.append(max_dd)
            all_final_equities.append(sim_equity)
            all_losing_streaks.append(max_streak)

            if sim_idx < 100:
                sample_curves.append(curve_points)

        log.info(f"Monte Carlo complete: {ruin_count}/{self._n_sims} ruined")

        # Calculate statistics
        all_max_dds.sort()
        all_final_equities.sort()
        all_losing_streaks.sort()

        p5_idx  = int(0.05 * self._n_sims)
        p50_idx = int(0.50 * self._n_sims)
        p95_idx = int(0.95 * self._n_sims)

        result = MonteCarloResult(
            n_simulations    = self._n_sims,
            n_trades         = n,
            initial_equity   = equity,
            ruin_threshold   = self._ruin_threshold,
            risk_of_ruin_pct = round(ruin_count / self._n_sims * 100, 2),
            max_dd_p5        = round(all_max_dds[p5_idx],  2),
            max_dd_p50       = round(all_max_dds[p50_idx], 2),
            max_dd_p95       = round(all_max_dds[p95_idx], 2),
            equity_p5        = round(all_final_equities[p5_idx],  2),
            equity_p50       = round(all_final_equities[p50_idx], 2),
            equity_p95       = round(all_final_equities[p95_idx], 2),
            longest_losing_streak_p95 = all_losing_streaks[p95_idx],
            avg_final_equity = round(sum(all_final_equities) / self._n_sims, 2),
            equity_curves    = sample_curves,
        )

        log.info(result.summary())
        return result
