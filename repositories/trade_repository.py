"""
repositories/trade_repository.py — CRUD abstraction for trade records.

WHY THIS FILE EXISTS
--------------------
Every trade from open to close must be persisted.
Without a repository, execution/, risk/, and dashboard/ would each write
their own raw SQL.  Change the schema → fix it in 5 places.

With a repository: change the schema here → only 1 place to fix.
All callers use typed methods like repo.insert(trade), repo.get_open().

USAGE:
    from repositories.trade_repository import TradeRepository
    from services.storage_service import storage

    repo = TradeRepository(storage)

    # On fill:
    trade_id = repo.insert(trade_event)

    # On close:
    repo.update_close(trade_id, close_price, close_reason, net_pnl)

    # Dashboard / performance analysis:
    open_trades = repo.get_open()
    today_summary = repo.get_daily_summary(date.today())
    recent = repo.get_paginated(page=1, page_size=50)
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from core.enums import Direction, TradeStatus
from core.logger import get_logger, LogCategory
from core.models.trade import TradeEvent
from services.storage_service import StorageService

log = get_logger("trade_repository", LogCategory.TRADING)


class TradeRepository:
    """
    All trade persistence operations go through this class.

    Inject StorageService at construction:
        repo = TradeRepository(storage)

    Thread-safe: delegates locking to StorageService.
    """

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage

    # ─── INSERT ──────────────────────────────────────────────────────────────

    def insert(self, trade: TradeEvent) -> str:
        """
        Persist a new trade record.  Returns the generated trade_id (UUID4).

        Call this when a trade is FILLED (not when submitted — wait for broker confirm).
        The trade_id is used for all subsequent updates to this trade.

        Parameters:
            trade: Fully populated TradeEvent from execution/broker_bridge.py
        Returns:
            trade_id: UUID4 string — store this to call update_close() later
        """
        trade_id = str(uuid.uuid4())
        open_time_str = trade.open_time.isoformat() if trade.open_time else ""
        direction_str = trade.direction.value if trade.direction else ""
        status_str    = trade.status.value if trade.status else ""

        sql = """
        INSERT OR REPLACE INTO trades (
            id, correlation_id, broker_ticket, symbol, direction, volume, status,
            requested_price, fill_price, stop_loss, take_profit, close_price,
            close_reason, slippage_pips, spread_at_entry, spread_cost_usd,
            commission, swap, total_cost_usd, gross_pnl, net_pnl,
            max_adverse_excursion, max_favorable_excursion,
            regime_at_entry, session_at_entry, score_at_entry, strategy,
            atr_at_entry, open_time, close_time, duration_minutes
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """
        params = (
            trade_id, trade.correlation_id, trade.broker_ticket,
            trade.symbol, direction_str, trade.volume, status_str,
            trade.requested_price, trade.fill_price, trade.stop_loss,
            trade.take_profit, trade.close_price, trade.close_reason,
            trade.slippage_pips, trade.spread_at_entry, trade.spread_cost_usd,
            trade.commission, trade.swap, trade.total_cost_usd,
            trade.gross_pnl, trade.net_pnl,
            trade.max_adverse_excursion, trade.max_favorable_excursion,
            trade.regime_at_entry, trade.session_at_entry, trade.score_at_entry,
            trade.strategy, trade.atr_at_entry,
            open_time_str, "", 0.0,
        )
        self._storage.execute_sqlite_write(sql, params)
        log.debug(f"Trade inserted: {trade_id} | {trade.symbol} {direction_str}")
        return trade_id

    # ─── UPDATE ──────────────────────────────────────────────────────────────

    def update_close(
        self,
        trade_id:    str,
        close_price: float,
        close_reason: str,
        gross_pnl:   float,
        net_pnl:     float,
        swap:        float = 0.0,
        close_time:  Optional[datetime] = None,
    ) -> None:
        """
        Update a trade record when the position is closed by the broker.

        Call this from execution/fill_manager.py when you receive a TRADE_CLOSED
        event from MT5.  The trade_id comes from the initial insert() call.
        """
        close_time_str = (close_time or datetime.utcnow()).isoformat()

        # Calculate duration if we have open_time
        rows = self.execute_sqlite(
            "SELECT open_time FROM trades WHERE id = ?", (trade_id,)
        )
        duration = 0.0
        if rows and rows[0]["open_time"]:
            try:
                open_dt = datetime.fromisoformat(rows[0]["open_time"])
                close_dt = datetime.fromisoformat(close_time_str)
                duration = round((close_dt - open_dt).total_seconds() / 60, 1)
            except Exception:
                pass

        sql = """
        UPDATE trades SET
            close_price     = ?,
            close_reason    = ?,
            gross_pnl       = ?,
            net_pnl         = ?,
            swap            = ?,
            close_time      = ?,
            duration_minutes = ?,
            status          = 'CLOSED'
        WHERE id = ?
        """
        self._storage.execute_sqlite_write(sql, (
            close_price, close_reason, gross_pnl, net_pnl,
            swap, close_time_str, duration, trade_id,
        ))
        log.info(f"Trade closed: {trade_id} | net_pnl={net_pnl:+.2f} reason={close_reason}")

    def update_status(self, trade_id: str, status: str) -> None:
        """Update trade status string (e.g. PENDING → FILLED → CLOSED)."""
        self._storage.execute_sqlite_write(
            "UPDATE trades SET status = ? WHERE id = ?",
            (status, trade_id),
        )

    def update_excursions(
        self, trade_id: str, mae_pips: float, mfe_pips: float
    ) -> None:
        """
        Update max adverse excursion (worst drawdown) and max favorable excursion
        (best unrealized profit) during a trade.  Call periodically while open.
        Used for post-trade analysis and stop improvement research.
        """
        self._storage.execute_sqlite_write(
            """UPDATE trades SET
               max_adverse_excursion   = ?,
               max_favorable_excursion = ?
               WHERE id = ?""",
            (mae_pips, mfe_pips, trade_id),
        )

    # ─── QUERIES ─────────────────────────────────────────────────────────────

    def get_by_id(self, trade_id: str) -> Optional[dict]:
        """Return one trade row as dict, or None if not found."""
        rows = self._storage.execute_sqlite(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        )
        return dict(rows[0]) if rows else None

    def get_open(self) -> list[dict]:
        """
        Return all trades with status = 'FILLED' (open positions).
        Called by recovery_manager to rebuild state after restart.
        Also called by correlation_guard to check exposure.
        """
        rows = self._storage.execute_sqlite(
            "SELECT * FROM trades WHERE status = 'FILLED' ORDER BY open_time DESC"
        )
        return [dict(r) for r in rows]

    def get_daily_trades(self, day: Optional[date] = None) -> list[dict]:
        """
        Return all trades opened on a given day (default: today UTC).
        Used by risk engine to enforce daily trade count limits.
        """
        target = (day or date.today()).isoformat()
        rows = self._storage.execute_sqlite(
            "SELECT * FROM trades WHERE DATE(open_time) = ? ORDER BY open_time",
            (target,),
        )
        return [dict(r) for r in rows]

    def get_paginated(
        self,
        page: int = 1,
        page_size: int = 50,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        """
        Return trades with optional filters and pagination.
        Used by dashboard Journal page.

        Parameters:
            page:      1-indexed page number
            page_size: rows per page
            symbol:    filter by symbol (e.g. "XAUUSD")
            strategy:  filter by strategy name
            status:    filter by trade status ("FILLED", "CLOSED", etc.)
        """
        wheres: list[str] = []
        params: list     = []

        if symbol:
            wheres.append("symbol = ?")
            params.append(symbol)
        if strategy:
            wheres.append("strategy = ?")
            params.append(strategy)
        if status:
            wheres.append("status = ?")
            params.append(status)

        where_clause = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        offset = (page - 1) * page_size
        params += [page_size, offset]

        sql = f"""
        SELECT * FROM trades {where_clause}
        ORDER BY open_time DESC
        LIMIT ? OFFSET ?
        """
        rows = self._storage.execute_sqlite(sql, tuple(params))
        return [dict(r) for r in rows]

    def get_daily_summary(self, day: Optional[date] = None) -> dict:
        """
        Return aggregated stats for a given day.

        Returns dict with:
            total_trades, wins, losses, win_rate,
            total_net_pnl, total_gross_pnl, total_costs,
            avg_slippage_pips, best_trade_pnl, worst_trade_pnl
        """
        target = (day or date.today()).isoformat()
        rows = self._storage.execute_sqlite(
            """SELECT
                COUNT(*)                          as total,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(net_pnl)                      as total_net_pnl,
                SUM(gross_pnl)                    as total_gross_pnl,
                SUM(total_cost_usd)               as total_costs,
                AVG(slippage_pips)                as avg_slippage,
                MAX(net_pnl)                      as best_pnl,
                MIN(net_pnl)                      as worst_pnl
            FROM trades WHERE DATE(open_time) = ? AND status = 'CLOSED'""",
            (target,),
        )
        if not rows:
            return {}
        r = dict(rows[0])
        total = r["total"] or 0
        wins  = r["wins"] or 0
        return {
            "total_trades":    total,
            "wins":            wins,
            "losses":          total - wins,
            "win_rate":        round(wins / total * 100, 1) if total else 0.0,
            "total_net_pnl":   round(r["total_net_pnl"] or 0.0, 2),
            "total_gross_pnl": round(r["total_gross_pnl"] or 0.0, 2),
            "total_costs":     round(r["total_costs"] or 0.0, 2),
            "avg_slippage_pips": round(r["avg_slippage"] or 0.0, 2),
            "best_trade_pnl":  round(r["best_pnl"] or 0.0, 2),
            "worst_trade_pnl": round(r["worst_pnl"] or 0.0, 2),
            "date":            target,
        }

    def get_performance_summary(self, days: int = 30) -> dict:
        """
        Return multi-day performance metrics.
        Used by dashboard Live Trading and Journal pages.
        """
        rows = self._storage.execute_sqlite(
            """SELECT
                COUNT(*)          as total,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(net_pnl)      as total_net_pnl,
                AVG(net_pnl)      as avg_pnl,
                AVG(slippage_pips) as avg_slippage,
                MAX(net_pnl)      as best,
                MIN(net_pnl)      as worst
            FROM trades
            WHERE status = 'CLOSED'
              AND DATE(open_time) >= DATE('now', ?)""",
            (f"-{days} days",),
        )
        if not rows:
            return {}
        r = dict(rows[0])
        total = r["total"] or 0
        wins  = r["wins"] or 0
        total_pnl = r["total_net_pnl"] or 0.0
        losses_pnl = abs(sum(
            d["net_pnl"] for d in self.get_paginated(page_size=9999)
            if d.get("net_pnl", 0) < 0
        ))
        wins_pnl = total_pnl + losses_pnl if losses_pnl else total_pnl
        pf = round(wins_pnl / losses_pnl, 2) if losses_pnl > 0 else 0.0
        return {
            "period_days":   days,
            "total_trades":  total,
            "wins":          wins,
            "losses":        total - wins,
            "win_rate":      round(wins / total * 100, 1) if total else 0.0,
            "total_net_pnl": round(total_pnl, 2),
            "profit_factor": pf,
            "avg_pnl":       round(r["avg_pnl"] or 0.0, 2),
            "avg_slippage":  round(r["avg_slippage"] or 0.0, 2),
            "best_trade":    round(r["best"] or 0.0, 2),
            "worst_trade":   round(r["worst"] or 0.0, 2),
        }

    def execute_sqlite(self, sql: str, params: tuple = ()) -> list:
        """Passthrough for direct queries in this class."""
        return self._storage.execute_sqlite(sql, params)
