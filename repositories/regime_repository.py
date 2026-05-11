"""
repositories/regime_repository.py — CRUD for regime history in DuckDB.

Regime changes are stored in DuckDB (time-series data).
The dashboard's Regime Monitor page reads from here.
The regime detector writes here after every classification.

IMPORTANT: DuckDB primary key constraint means duplicate (symbol, timeframe, time)
rows will fail.  The regime detector should only insert when regime CHANGES
or on a new H1 candle close — not every tick.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from core.logger import get_logger, LogCategory
from core.models.regime import RegimeState
from services.storage_service import StorageService

log = get_logger("regime_repository", LogCategory.DATA)


class RegimeRepository:

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage
        self._extended_schema_checked = False

    def _ensure_extended_schema(self) -> None:
        if self._extended_schema_checked:
            return
        self._extended_schema_checked = True
        alters = [
            "ALTER TABLE regime_history ADD COLUMN regime_label VARCHAR",
            "ALTER TABLE regime_history ADD COLUMN probabilities_json VARCHAR",
            "ALTER TABLE regime_history ADD COLUMN transition_state VARCHAR",
            "ALTER TABLE regime_history ADD COLUMN structure_label VARCHAR",
            "ALTER TABLE regime_history ADD COLUMN rsi DOUBLE",
            "ALTER TABLE regime_history ADD COLUMN volume_signal VARCHAR",
            "ALTER TABLE regime_history ADD COLUMN candles_used INTEGER",
            "ALTER TABLE regime_history ADD COLUMN lookback_years DOUBLE",
        ]
        for ddl in alters:
            try:
                self._storage.execute_duckdb_write(ddl)
            except Exception:
                # Column may already exist or DB may be read-only in dashboard process.
                continue

    def insert(self, regime: RegimeState) -> None:
        """
        Persist one regime classification to DuckDB.
        Call from regime_detector after every candle close.
        Uses INSERT OR IGNORE semantics (DuckDB PRIMARY KEY).
        """
        if not self._storage._duckdb_conn:
            return  # graceful: DuckDB not available
        self._ensure_extended_schema()

        regime_str  = regime.regime.value  if regime.regime  else "UNKNOWN"
        session_str = regime.session.value if regime.session else "OFF"

        sql = """
        INSERT OR IGNORE INTO regime_history
        (symbol, timeframe, time, regime, adx, atr, atr_percentile,
         confidence, session, bars_in_regime, regime_label, probabilities_json,
         transition_state, structure_label, rsi, volume_signal, candles_used, lookback_years)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        self._storage.execute_duckdb_write(sql, (
            regime.symbol, regime.timeframe, regime.timestamp,
            regime_str, regime.adx, regime.atr, regime.atr_percentile,
            regime.confidence, session_str, regime.bars_in_regime,
            regime.regime_label,
            json.dumps(regime.probabilities or {}),
            regime.transition_state,
            regime.structure_label,
            regime.rsi,
            regime.volume_signal,
            regime.candles_used,
            regime.lookback_years,
        ))
        log.debug(f"Regime saved: {regime.symbol} {regime_str} conf={regime.confidence:.0%}")

    def get_history(
        self,
        symbol: str,
        timeframe: str = "H1",
        limit: int = 200,
    ) -> list[dict]:
        """
        Return last N regime records for a symbol.
        Used by dashboard Regime Monitor page to draw regime timeline.
        """
        rows = self._storage.execute_duckdb(
            """SELECT symbol, timeframe, time, regime, adx, atr, atr_percentile, confidence, session, bars_in_regime,
                      COALESCE(regime_label, regime) as regime_label,
                      probabilities_json, transition_state, structure_label, rsi, volume_signal,
                      candles_used, lookback_years
               FROM regime_history
               WHERE symbol = ? AND timeframe = ?
               ORDER BY time DESC LIMIT ?""",
            (symbol, timeframe, limit),
        )
        cols = ["symbol", "timeframe", "time", "regime", "adx", "atr",
                "atr_percentile", "confidence", "session", "bars_in_regime",
                "regime_label", "probabilities_json", "transition_state", "structure_label",
                "rsi", "volume_signal", "candles_used", "lookback_years"]
        return [dict(zip(cols, r)) for r in rows]

    def get_latest(self, symbol: str, timeframe: str = "H1") -> Optional[dict]:
        """Return the most recent regime for a symbol."""
        rows = self._storage.execute_duckdb(
            """SELECT * FROM regime_history
               WHERE symbol = ? AND timeframe = ?
               ORDER BY time DESC LIMIT 1""",
            (symbol, timeframe),
        )
        if not rows:
            return None
        cols = ["symbol", "timeframe", "time", "regime", "adx", "atr",
                "atr_percentile", "confidence", "session", "bars_in_regime"]
        return dict(zip(cols, rows[0]))

    def get_regime_distribution(self, symbol: str, days: int = 30) -> dict:
        """
        Return count of each regime type over N days.
        Used by dashboard analytics to show how often each regime occurs.
        """
        rows = self._storage.execute_duckdb(
            """SELECT COALESCE(regime_label, regime) as label, COUNT(*) as cnt
               FROM regime_history
               WHERE symbol = ? AND time >= CURRENT_TIMESTAMP - INTERVAL ? DAY
               GROUP BY label ORDER BY cnt DESC""",
            (symbol, days),
        )
        return {r[0]: r[1] for r in rows} if rows else {}

    def get_regime_distribution_by_years(self, symbol: str, timeframe: str, years: float) -> dict:
        days = max(7, int(years * 365))
        rows = self._storage.execute_duckdb(
            """SELECT COALESCE(regime_label, regime) as label, COUNT(*) as cnt
               FROM regime_history
               WHERE symbol = ? AND timeframe = ? AND time >= CURRENT_TIMESTAMP - INTERVAL ? DAY
               GROUP BY label ORDER BY cnt DESC""",
            (symbol, timeframe, days),
        )
        return {r[0]: int(r[1]) for r in rows} if rows else {}

    def get_regime_performance(self, years: float) -> list[dict]:
        """
        Performance analytics from SQLite closed trades grouped by regime_at_entry.
        """
        days = max(7, int(years * 365))
        rows = self._storage.execute_sqlite(
            """
            SELECT
                COALESCE(regime_at_entry, 'UNKNOWN') AS regime,
                strategy,
                close_time,
                net_pnl
            FROM trades
            WHERE status = 'CLOSED'
              AND close_time IS NOT NULL
              AND datetime(close_time) <= datetime('now', 'start of day', '-1 day')
              AND datetime(close_time) >= datetime('now', ?)
            ORDER BY datetime(close_time) ASC
            """,
            (f"-{days} days",),
        )
        grouped: dict[str, list[dict]] = {}
        for r in rows:
            key = str(r["regime"] or "UNKNOWN")
            grouped.setdefault(key, []).append(
                {"pnl": float(r["net_pnl"] or 0.0), "strategy": str(r["strategy"] or "UNKNOWN")}
            )

        out: list[dict] = []
        for regime, arr in grouped.items():
            pnls = [a["pnl"] for a in arr]
            wins = sum(1 for p in pnls if p > 0)
            losses = [p for p in pnls if p < 0]
            gains = [p for p in pnls if p > 0]
            gross_profit = sum(gains)
            gross_loss = abs(sum(losses))
            pf = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
            equity = 0.0
            peak = 0.0
            max_dd = 0.0
            for p in pnls:
                equity += p
                peak = max(peak, equity)
                max_dd = min(max_dd, equity - peak)
            strategy_counts: dict[str, int] = {}
            for a in arr:
                strategy_counts[a["strategy"]] = strategy_counts.get(a["strategy"], 0) + 1
            best_strategy = max(strategy_counts, key=strategy_counts.get) if strategy_counts else "UNKNOWN"
            out.append(
                {
                    "regime": regime,
                    "trades": len(pnls),
                    "win_rate_pct": round((wins / len(pnls) * 100.0) if pnls else 0.0, 2),
                    "profit_factor": round(pf, 2),
                    "max_drawdown": round(max_dd, 2),
                    "avg_duration_bars": None,
                    "best_strategy": best_strategy,
                }
            )
        out.sort(key=lambda x: x["trades"], reverse=True)
        return out
