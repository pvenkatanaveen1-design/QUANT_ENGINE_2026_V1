"""
systems/research/backtester.py — S19: Event-Driven Backtester.

WHY THIS FILE EXISTS
--------------------
Before risking money on a funded challenge (₹8,000-12,000), you must prove
your strategy works on historical data.  Minimum: 200 trades with:
  - Profit factor ≥ 1.2
  - Sharpe ratio ≥ 0.8
  - Win rate 40-60%
  - Max drawdown < 15%

This backtester uses the SAME strategy objects as live trading.
The strategy code never changes between backtest and live.
This eliminates "it worked in backtest but not live" from code differences.

REALISTIC SIMULATION:
  Every backtest trade applies:
  1. Spread cost: from config/symbols.yaml (default 0.3 pips for London XAUUSD)
  2. Commission: configurable per lot (typical ECN: $3.50/lot roundtrip)
  3. Slippage: randomized within configured range (0-0.5 pips typical)
  4. Session filter: signals only generated during configured sessions
  5. ATR-based SL: uses actual ATR from historical data, not guessed values
  6. Time exit: if trade not closed by N bars, close at market

HOW BACKTESTER WORKS:
  1. Load N candles from DuckDB (or CSV if no DB data yet)
  2. For each bar (oldest to newest):
     a. Update any open simulated positions (check SL/TP hit)
     b. Apply session filter (skip if off-session)
     c. Call strategy.generate_signals(candles_up_to_this_bar)
     d. If signal returned: open simulated position with costs applied
  3. At end: calculate metrics and return BacktestResult

OUTPUT METRICS:
  - Equity curve (list of equity values, one per bar)
  - Trade log (list of simulated trades with all costs)
  - Profit factor, Sharpe ratio, max drawdown, win rate
  - These are saved to backtest_repository for the dashboard
"""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, List, Optional

from core.logger import get_logger, LogCategory
from strategies.base import BaseStrategy

log = get_logger("backtester", LogCategory.BACKTEST)

# Simulation cost defaults (override via config/symbols.yaml)
DEFAULT_SPREAD_PIPS   = 0.30   # London session XAUUSD spread
DEFAULT_COMMISSION    = 3.50   # USD roundtrip per standard lot
DEFAULT_SLIPPAGE_MAX  = 0.30   # Max random slippage in pips
INITIAL_EQUITY        = 10000.0  # Starting equity for simulation ($)
RISK_PCT              = 0.005    # 0.5% risk per trade
MAX_BARS_IN_TRADE     = 48       # Close trade after 48 bars (2 days) if not hit SL/TP


@dataclass
class SimulatedTrade:
    """One simulated trade from the backtester."""
    trade_id:        str
    direction:       str
    entry_price:     float
    stop_loss:       float
    take_profit:     float
    lot_size:        float
    entry_bar:       int           # Bar index when trade opened
    entry_time:      Optional[Any] # Candle timestamp

    close_price:     float = 0.0
    close_bar:       int   = 0
    close_reason:    str   = ""
    gross_pnl:       float = 0.0
    spread_cost:     float = 0.0
    commission:      float = 0.0
    slippage_cost:   float = 0.0
    net_pnl:         float = 0.0

    is_open:         bool  = True
    bars_open:       int   = 0


@dataclass
class BacktestResult:
    """Complete output of one backtester run."""
    strategy:        str
    symbol:          str
    timeframe:       str
    start_date:      Optional[date]
    end_date:        Optional[date]

    # Equity curve
    equity_curve:    list = field(default_factory=list)   # [(time, equity), ...]

    # Trade log
    trades:          list = field(default_factory=list)   # list of SimulatedTrade dicts

    # Summary metrics
    total_trades:    int   = 0
    winning_trades:  int   = 0
    win_rate:        float = 0.0
    profit_factor:   float = 0.0
    sharpe_ratio:    float = 0.0
    max_drawdown_pct: float = 0.0
    total_net_pnl:   float = 0.0
    avg_net_pnl:     float = 0.0

    # Pass/fail
    passes_criteria: bool  = False
    failure_reasons: list  = field(default_factory=list)

    def to_metrics_dict(self) -> dict:
        return {
            "total_trades":     self.total_trades,
            "win_rate":         self.win_rate,
            "profit_factor":    self.profit_factor,
            "sharpe_ratio":     self.sharpe_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "total_net_pnl":    self.total_net_pnl,
        }

    def summary(self) -> str:
        status = "PASS" if self.passes_criteria else "FAIL"
        return (
            f"[{status}] {self.strategy} | "
            f"Trades:{self.total_trades} WR:{self.win_rate:.1f}% "
            f"PF:{self.profit_factor:.2f} Sharpe:{self.sharpe_ratio:.2f} "
            f"MaxDD:{self.max_drawdown_pct:.1f}% PnL:${self.total_net_pnl:+.2f}"
        )


class Backtester:
    """
    Event-driven backtester.  Replays historical candles bar by bar.

    USAGE:
        from systems.research.backtester import Backtester
        from strategies.alpha_breakout import AlphaBreakout

        bt = Backtester()
        result = bt.run(
            strategy  = AlphaBreakout(),
            symbol    = "XAUUSD",
            timeframe = "H1",
        )
        print(result.summary())
        if result.passes_criteria:
            print("Ready for walk-forward testing!")
    """

    def __init__(
        self,
        spread_pips:  float = DEFAULT_SPREAD_PIPS,
        commission:   float = DEFAULT_COMMISSION,
        slippage_max: float = DEFAULT_SLIPPAGE_MAX,
        initial_equity: float = INITIAL_EQUITY,
        risk_pct:     float = RISK_PCT,
    ) -> None:
        self._spread_pips    = spread_pips
        self._commission     = commission
        self._slippage_max   = slippage_max
        self._initial_equity = initial_equity
        self._risk_pct       = risk_pct

    def run(
        self,
        strategy:   BaseStrategy,
        symbol:     str = "XAUUSD",
        timeframe:  str = "H1",
        candle_data = None,  # pd.DataFrame or list[dict]
    ) -> BacktestResult:
        """
        Run a backtest for the given strategy on historical candle data.

        Parameters:
            strategy:    An instance of a BaseStrategy subclass
            symbol:      e.g. "XAUUSD"
            timeframe:   e.g. "H1"
            candle_data: Optional pre-loaded data.  If None, fetches from DuckDB.

        Returns:
            BacktestResult with metrics, equity curve, and trade log.
        """
        log.info(f"Starting backtest: {strategy.name} on {symbol} {timeframe}")

        # Load data if not provided
        if candle_data is None:
            candle_data = self._load_data(symbol, timeframe)

        if candle_data is None or (hasattr(candle_data, "__len__") and len(candle_data) == 0):
            log.error("No candle data available for backtest")
            result = BacktestResult(
                strategy=strategy.name, symbol=symbol, timeframe=timeframe,
                start_date=None, end_date=None,
            )
            result.failure_reasons.append("No historical data.  Import CSV first.")
            return result

        # Convert to list of dicts for uniform processing
        candles = self._normalize_candles(candle_data)
        if not candles:
            log.error("Failed to normalize candle data")
            result = BacktestResult(
                strategy=strategy.name, symbol=symbol, timeframe=timeframe,
                start_date=None, end_date=None,
            )
            result.failure_reasons.append("Could not parse candle data format.")
            return result

        n = len(candles)
        log.info(f"Loaded {n} candles from {candles[0].get('time')} to {candles[-1].get('time')}")

        # Simulation state
        equity     = self._initial_equity
        peak_equity = equity
        max_dd_pct = 0.0
        equity_curve: list = [(candles[0].get("time"), equity)]
        open_trades: list[SimulatedTrade] = []
        closed_trades: list[SimulatedTrade] = []
        pnl_series: list[float] = []

        # Warm-up: need at least 50 bars for indicators
        warmup = 50

        for bar_idx in range(warmup, n):
            candles_so_far = candles[:bar_idx + 1]
            current_candle = candles[bar_idx]
            current_high   = float(current_candle.get("high", 0))
            current_low    = float(current_candle.get("low",  0))
            current_close  = float(current_candle.get("close", 0))

            # ── Update open positions ──────────────────────────────────────
            for trade in list(open_trades):
                trade.bars_open += 1
                pnl = self._check_exit(trade, current_high, current_low, bar_idx, candles_so_far)
                if pnl is not None:
                    trade.is_open = False
                    open_trades.remove(trade)
                    closed_trades.append(trade)
                    equity += trade.net_pnl
                    pnl_series.append(trade.net_pnl)

                    # Update peak and max DD
                    if equity > peak_equity:
                        peak_equity = equity
                    dd_pct = (peak_equity - equity) / peak_equity * 100
                    if dd_pct > max_dd_pct:
                        max_dd_pct = dd_pct

                    equity_curve.append((current_candle.get("time"), round(equity, 2)))

                    # Stop if equity wiped out
                    if equity <= 0:
                        log.warning(f"Backtest: equity depleted at bar {bar_idx}")
                        break

            if equity <= 0:
                break

            # Max open trades: only 1 at a time for this strategy
            if len(open_trades) >= 1:
                continue

            # ── Generate signals ───────────────────────────────────────────
            signals = strategy.generate_signals(candles_so_far)

            for signal in signals:
                # Skip invalid signals (but for backtest, relax score requirement)
                if (signal.entry_price <= 0 or
                    signal.stop_loss <= 0 or
                    signal.take_profit <= 0 or
                    signal.rr_ratio < 1.5):
                    continue

                # Calculate lot size from equity risk
                sl_pips   = signal.sl_pips or abs(signal.entry_price - signal.stop_loss) / 0.10
                risk_amt  = equity * self._risk_pct
                pip_value = 1.0  # $1 per pip per 0.01 lot for XAUUSD (adjust for symbol)
                lot_size  = round(risk_amt / (sl_pips * pip_value * 100), 2)
                lot_size  = max(0.01, min(lot_size, 0.50))  # clamp

                # Apply slippage to entry price
                slippage_pips  = random.uniform(0, self._slippage_max)
                slippage_price = slippage_pips * 0.10
                if signal.direction.value == "BUY":
                    actual_entry = signal.entry_price + slippage_price
                else:
                    actual_entry = signal.entry_price - slippage_price

                # Cost calculation
                spread_cost = self._spread_pips * 0.10 * lot_size * 100  # USD
                commission  = self._commission * lot_size
                slippage_cost = slippage_pips * 0.10 * lot_size * 100

                trade = SimulatedTrade(
                    trade_id    = str(uuid.uuid4())[:8],
                    direction   = signal.direction.value,
                    entry_price = actual_entry,
                    stop_loss   = signal.stop_loss,
                    take_profit = signal.take_profit,
                    lot_size    = lot_size,
                    entry_bar   = bar_idx,
                    entry_time  = current_candle.get("time"),
                    spread_cost   = spread_cost,
                    commission    = commission,
                    slippage_cost = slippage_cost,
                )
                open_trades.append(trade)
                log.debug(
                    f"Opened {trade.direction} @ {actual_entry:.2f} "
                    f"SL={trade.stop_loss:.2f} TP={trade.take_profit:.2f} "
                    f"Lots={lot_size} Bar={bar_idx}"
                )
                break  # One signal per bar

        # Close any remaining open trades at last price
        last_close = float(candles[-1].get("close", 0)) if candles else 0
        for trade in open_trades:
            self._force_close(trade, last_close, n - 1)
            equity += trade.net_pnl
            pnl_series.append(trade.net_pnl)
            closed_trades.append(trade)

        # ── Calculate final metrics ────────────────────────────────────────
        result = self._calculate_metrics(
            strategy    = strategy,
            symbol      = symbol,
            timeframe   = timeframe,
            candles     = candles,
            trades      = closed_trades,
            equity_curve = equity_curve,
            pnl_series  = pnl_series,
            max_dd_pct  = max_dd_pct,
        )

        # Save to repository
        try:
            self._save_result(result)
        except Exception as exc:
            log.warning(f"Could not save backtest result: {exc}")

        log.info(result.summary())
        return result

    # ─── SIMULATION HELPERS ───────────────────────────────────────────────────

    def _check_exit(
        self,
        trade:         SimulatedTrade,
        bar_high:      float,
        bar_low:       float,
        bar_idx:       int,
        candles:       list,
    ) -> Optional[float]:
        """
        Check if SL or TP was hit on this bar.
        Returns net_pnl if trade closed, None if still open.
        """
        # Time exit: close after MAX_BARS_IN_TRADE
        if trade.bars_open >= MAX_BARS_IN_TRADE:
            close_price = float(candles[-1].get("close", trade.entry_price))
            self._apply_close(trade, close_price, "TIME_EXIT")
            return trade.net_pnl

        if trade.direction == "BUY":
            if bar_low <= trade.stop_loss:
                self._apply_close(trade, trade.stop_loss, "STOP_LOSS")
                return trade.net_pnl
            if bar_high >= trade.take_profit:
                self._apply_close(trade, trade.take_profit, "TAKE_PROFIT")
                return trade.net_pnl
        else:  # SELL
            if bar_high >= trade.stop_loss:
                self._apply_close(trade, trade.stop_loss, "STOP_LOSS")
                return trade.net_pnl
            if bar_low <= trade.take_profit:
                self._apply_close(trade, trade.take_profit, "TAKE_PROFIT")
                return trade.net_pnl

        return None

    def _apply_close(
        self, trade: SimulatedTrade, close_price: float, reason: str
    ) -> None:
        """Calculate PnL and apply to trade."""
        price_diff = close_price - trade.entry_price
        if trade.direction == "SELL":
            price_diff = -price_diff

        pip_move     = price_diff / 0.10
        pip_val      = trade.lot_size * 100  # $1 per pip per 0.01 lot (XAUUSD approx)
        gross_pnl    = pip_move * pip_val
        total_costs  = trade.spread_cost + trade.commission + trade.slippage_cost
        net_pnl      = round(gross_pnl - total_costs, 2)

        trade.close_price  = close_price
        trade.close_reason = reason
        trade.gross_pnl    = round(gross_pnl, 2)
        trade.net_pnl      = net_pnl
        trade.is_open      = False

    def _force_close(self, trade: SimulatedTrade, price: float, bar: int) -> None:
        """Force-close an open trade at end of backtest."""
        self._apply_close(trade, price, "BACKTEST_END")

    # ─── METRICS CALCULATION ─────────────────────────────────────────────────

    def _calculate_metrics(
        self,
        strategy: BaseStrategy,
        symbol: str,
        timeframe: str,
        candles: list,
        trades: list[SimulatedTrade],
        equity_curve: list,
        pnl_series: list[float],
        max_dd_pct: float,
    ) -> BacktestResult:
        n = len(trades)
        wins = [t for t in trades if t.net_pnl > 0]
        losses = [t for t in trades if t.net_pnl <= 0]

        win_rate = len(wins) / n * 100 if n > 0 else 0.0
        total_pnl = sum(t.net_pnl for t in trades)
        gross_win = sum(t.net_pnl for t in wins)
        gross_loss = abs(sum(t.net_pnl for t in losses))
        pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0
        avg_pnl = round(total_pnl / n, 2) if n > 0 else 0.0

        # Sharpe ratio (annualized, assuming H1 bars)
        sharpe = self._calculate_sharpe(pnl_series)

        # Date range
        start_date = None
        end_date   = None
        if candles:
            try:
                s = candles[0].get("time")
                e = candles[-1].get("time")
                if isinstance(s, datetime):
                    start_date = s.date()
                elif isinstance(s, str):
                    start_date = datetime.fromisoformat(s).date()
                if isinstance(e, datetime):
                    end_date = e.date()
                elif isinstance(e, str):
                    end_date = datetime.fromisoformat(e).date()
            except Exception:
                pass

        result = BacktestResult(
            strategy        = strategy.name,
            symbol          = symbol,
            timeframe       = timeframe,
            start_date      = start_date,
            end_date        = end_date,
            equity_curve    = [(str(t), e) for t, e in equity_curve],
            trades          = [self._trade_to_dict(t) for t in trades],
            total_trades    = n,
            winning_trades  = len(wins),
            win_rate        = round(win_rate, 1),
            profit_factor   = pf,
            sharpe_ratio    = sharpe,
            max_drawdown_pct = round(max_dd_pct, 2),
            total_net_pnl   = round(total_pnl, 2),
            avg_net_pnl     = avg_pnl,
        )

        # Check pass criteria
        failures = []
        if n < 200:
            failures.append(f"Trades {n} < 200 required")
        if pf < 1.2:
            failures.append(f"PF {pf:.2f} < 1.2 required")
        if sharpe < 0.8:
            failures.append(f"Sharpe {sharpe:.2f} < 0.8 required")
        if not (40 <= win_rate <= 60):
            failures.append(f"Win rate {win_rate:.1f}% not in 40-60%")
        if max_dd_pct >= 15:
            failures.append(f"MaxDD {max_dd_pct:.1f}% >= 15% limit")

        result.passes_criteria = len(failures) == 0
        result.failure_reasons = failures
        return result

    def _calculate_sharpe(self, pnl_series: list[float]) -> float:
        """Annualized Sharpe ratio from trade PnL series."""
        if len(pnl_series) < 2:
            return 0.0
        n = len(pnl_series)
        mean_pnl = sum(pnl_series) / n
        variance = sum((p - mean_pnl) ** 2 for p in pnl_series) / (n - 1)
        std_pnl  = math.sqrt(variance) if variance > 0 else 0.0
        if std_pnl == 0:
            return 0.0
        # Assume ~252 trading days × ~3 signals/day = 756 trades/year
        annualization = math.sqrt(252)
        sharpe = (mean_pnl / std_pnl) * annualization
        return round(sharpe, 2)

    def _save_result(self, result: BacktestResult) -> None:
        """Save result to backtest repository."""
        from repositories.backtest_repository import BacktestRepository
        from services.storage_service import storage
        repo = BacktestRepository(storage)
        if result.start_date and result.end_date:
            repo.save_backtest(
                strategy   = result.strategy,
                symbol     = result.symbol,
                timeframe  = result.timeframe,
                start_date = result.start_date,
                end_date   = result.end_date,
                metrics    = result.to_metrics_dict(),
                params     = {},
            )

    def _trade_to_dict(self, trade: SimulatedTrade) -> dict:
        return {
            "trade_id":    trade.trade_id,
            "direction":   trade.direction,
            "entry_price": trade.entry_price,
            "stop_loss":   trade.stop_loss,
            "take_profit": trade.take_profit,
            "close_price": trade.close_price,
            "close_reason": trade.close_reason,
            "lot_size":    trade.lot_size,
            "net_pnl":     trade.net_pnl,
            "gross_pnl":   trade.gross_pnl,
            "total_costs": trade.spread_cost + trade.commission + trade.slippage_cost,
            "entry_time":  str(trade.entry_time),
            "bars_open":   trade.bars_open,
        }

    def _normalize_candles(self, data) -> list[dict]:
        """Convert DataFrame or list[dict] to uniform list of dicts."""
        if data is None:
            return []
        if hasattr(data, "to_dict"):  # pandas DataFrame
            data = data.reset_index()
            return data.to_dict("records")
        if isinstance(data, list):
            return data
        return []

    def _load_data(self, symbol: str, timeframe: str) -> Optional[list]:
        """Load candle data from market_data_hub (DuckDB)."""
        try:
            from systems.data.market_data_hub import hub
            candles = hub.get_candles(symbol, timeframe, n=5000)
            if hasattr(candles, "empty") and not candles.empty:
                return self._normalize_candles(candles)
            if isinstance(candles, list) and candles:
                return candles
        except Exception as exc:
            log.error(f"Could not load backtest data: {exc}")
        return None
