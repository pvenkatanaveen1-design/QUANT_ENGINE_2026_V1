"""
repositories/backtest_repository.py — Persist and retrieve backtest results.

Every backtest run is saved with its full metrics and parameters.
This creates a versioned history of strategy performance across:
  - Different time periods
  - Different parameter sets
  - Different market conditions

WHY TRACK PARAMETERS?
  The optimizer (Optuna) runs many backtest combinations.
  Without saving params → metrics, you cannot reproduce results.
  With params saved: you know exactly which ATR multiplier gave Sharpe 1.2.

WALK FORWARD RESULTS:
  Each walk-forward run creates one backtest_result (for the full period)
  plus one walk_forward_result per window.

USAGE:
    repo = BacktestRepository(storage)
    run_id = repo.save_backtest(strategy="alpha_breakout", metrics={...}, params={...})
    results = repo.get_best_by_sharpe(strategy="alpha_breakout", top_n=5)
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Optional

from core.logger import get_logger, LogCategory
from services.storage_service import StorageService

log = get_logger("backtest_repository", LogCategory.BACKTEST)


class BacktestRepository:

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage

    # ─── BACKTEST RESULTS ────────────────────────────────────────────────────

    def save_backtest(
        self,
        strategy:      str,
        symbol:        str,
        timeframe:     str,
        start_date:    date,
        end_date:      date,
        metrics:       dict,
        params:        dict,
    ) -> str:
        """
        Save a complete backtest result to DuckDB.
        Returns the run ID (UUID4) for linking walk-forward windows.

        Parameters:
            strategy:   e.g. "alpha_breakout"
            symbol:     e.g. "XAUUSD"
            timeframe:  e.g. "H1"
            start_date: backtest start
            end_date:   backtest end
            metrics:    dict with keys: total_trades, win_rate, profit_factor,
                        sharpe_ratio, max_drawdown_pct, total_net_pnl
            params:     dict of strategy parameters used in this run
        """
        run_id = str(uuid.uuid4())
        sql = """
        INSERT INTO backtest_results (
            id, strategy, symbol, timeframe, start_date, end_date,
            total_trades, winning_trades, win_rate, profit_factor,
            sharpe_ratio, max_drawdown_pct, total_net_pnl,
            params_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        total  = metrics.get("total_trades", 0)
        wr     = metrics.get("win_rate", 0.0)
        wins   = int(total * wr / 100) if total else 0
        params = (
            run_id, strategy, symbol, timeframe,
            start_date.isoformat(), end_date.isoformat(),
            total, wins,
            metrics.get("win_rate", 0.0),
            metrics.get("profit_factor", 0.0),
            metrics.get("sharpe_ratio", 0.0),
            metrics.get("max_drawdown_pct", 0.0),
            metrics.get("total_net_pnl", 0.0),
            json.dumps(params),
            datetime.utcnow().isoformat(),
        )
        self._storage.execute_duckdb_write(sql, params)
        log.info(
            f"Backtest saved: {strategy} {symbol} "
            f"PF={metrics.get('profit_factor', 0):.2f} "
            f"Sharpe={metrics.get('sharpe_ratio', 0):.2f} id={run_id}"
        )
        return run_id

    def save_walk_forward_window(
        self,
        backtest_id:   str,
        window_index:  int,
        train_start:   date,
        train_end:     date,
        test_start:    date,
        test_end:      date,
        in_sample_pf:  float,
        out_sample_pf: float,
        params:        dict,
    ) -> str:
        """Save one OOS window from a walk-forward test."""
        wf_id = str(uuid.uuid4())
        sql = """
        INSERT INTO walk_forward_results (
            id, backtest_id, window_index,
            train_start, train_end, test_start, test_end,
            in_sample_pf, out_sample_pf, params_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """
        self._storage.execute_duckdb_write(sql, (
            wf_id, backtest_id, window_index,
            train_start.isoformat(), train_end.isoformat(),
            test_start.isoformat(), test_end.isoformat(),
            in_sample_pf, out_sample_pf,
            json.dumps(params), datetime.utcnow().isoformat(),
        ))
        return wf_id

    # ─── QUERIES ─────────────────────────────────────────────────────────────

    def get_all(
        self,
        strategy: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return backtest results, newest first."""
        where = "WHERE strategy = ?" if strategy else ""
        params_q = (strategy, limit) if strategy else (limit,)
        rows = self._storage.execute_duckdb(
            f"SELECT * FROM backtest_results {where} ORDER BY created_at DESC LIMIT ?",
            params_q,
        )
        cols = ["id", "strategy", "symbol", "timeframe", "start_date", "end_date",
                "total_trades", "winning_trades", "win_rate", "profit_factor",
                "sharpe_ratio", "max_drawdown_pct", "total_net_pnl",
                "params_json", "created_at"]
        return [dict(zip(cols, r)) for r in rows]

    def get_best_by_sharpe(self, strategy: str, top_n: int = 5) -> list[dict]:
        """Return top N backtest runs by Sharpe ratio.  Used by optimizer."""
        rows = self._storage.execute_duckdb(
            """SELECT * FROM backtest_results
               WHERE strategy = ? ORDER BY sharpe_ratio DESC LIMIT ?""",
            (strategy, top_n),
        )
        cols = ["id", "strategy", "symbol", "timeframe", "start_date", "end_date",
                "total_trades", "winning_trades", "win_rate", "profit_factor",
                "sharpe_ratio", "max_drawdown_pct", "total_net_pnl",
                "params_json", "created_at"]
        return [dict(zip(cols, r)) for r in rows]

    def get_walk_forward_windows(self, backtest_id: str) -> list[dict]:
        """Return all OOS windows for a given walk-forward run."""
        rows = self._storage.execute_duckdb(
            """SELECT * FROM walk_forward_results
               WHERE backtest_id = ? ORDER BY window_index""",
            (backtest_id,),
        )
        cols = ["id", "backtest_id", "window_index", "train_start", "train_end",
                "test_start", "test_end", "in_sample_pf", "out_sample_pf",
                "params_json", "created_at"]
        return [dict(zip(cols, r)) for r in rows]

    def passes_minimum_criteria(self, run_id: str) -> tuple[bool, list[str]]:
        """
        Check if a backtest run passes the minimum criteria for live trading.

        Criteria (from master plan):
            total_trades >= 200
            profit_factor >= 1.2
            sharpe_ratio >= 0.8
            win_rate 40-60%
            max_drawdown_pct < 15%

        Returns (passed: bool, failures: list[str])
        """
        rows = self._storage.execute_duckdb(
            "SELECT * FROM backtest_results WHERE id = ?", (run_id,)
        )
        if not rows:
            return False, ["Backtest ID not found"]

        r = rows[0]
        cols = ["id", "strategy", "symbol", "timeframe", "start_date", "end_date",
                "total_trades", "winning_trades", "win_rate", "profit_factor",
                "sharpe_ratio", "max_drawdown_pct", "total_net_pnl",
                "params_json", "created_at"]
        data = dict(zip(cols, r))

        failures = []
        if data["total_trades"] < 200:
            failures.append(f"Trades {data['total_trades']} < 200 required")
        if data["profit_factor"] < 1.2:
            failures.append(f"PF {data['profit_factor']:.2f} < 1.2 required")
        if data["sharpe_ratio"] < 0.8:
            failures.append(f"Sharpe {data['sharpe_ratio']:.2f} < 0.8 required")
        if not (40 <= data["win_rate"] <= 60):
            failures.append(f"Win rate {data['win_rate']:.1f}% not in 40-60% range")
        if data["max_drawdown_pct"] >= 15:
            failures.append(f"Max DD {data['max_drawdown_pct']:.1f}% >= 15% limit")

        return len(failures) == 0, failures
