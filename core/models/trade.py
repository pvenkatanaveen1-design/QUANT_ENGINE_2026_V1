"""
core/models/trade.py — TradeEvent dataclass.

WHY THIS FILE EXISTS
--------------------
Every position opened on the broker must become a TradeEvent.
journal/trade_logger.py writes this to SQLite.
monitoring/reconciliation.py compares this against broker records.
The performance dashboard reads these to calculate statistics.

correlation_id links back to the SignalEvent that caused this trade.
Every metric — slippage, cost, PnL — is captured here for analysis.

2026 FOREX REALITY NOTES
-------------------------
- slippage_pips: On ECN brokers, XAUUSD slippage is typically 0.0–0.5 pips
  in liquid sessions.  Above 1.0 pip slippage is a red flag — check broker.
  NEVER ignore slippage — it eats into your edge over hundreds of trades.

- Total cost = spread + commission + swap.
  Example on 0.10 lot XAUUSD London trade (open and close):
    Spread:     0.25 pips × 0.10 lot × $1/pip = $0.25
    Commission: ~$3.50 roundtrip (ECN broker typical)
    Net cost:   ~$3.75 per roundtrip
  At $50 risk per trade, $3.75 is 7.5% of your risk!  Count every pip.

- close_reason enum options match CloseReason in enums.py.
  Important for post-analysis: what % closed via TP vs SL vs TIME_EXIT?
  TIME_EXIT should be < 20% of trades if your TP levels are realistic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from core.enums import Direction, TradeStatus, CloseReason


@dataclass
class TradeEvent:
    """
    Complete record of one trade from open to close.
    Created when order is FILLED.  Updated on close.
    Written to SQLite by journal/trade_logger.py.
    """

    # ─── IDENTITY ────────────────────────────────────────────────────────────
    correlation_id: str = ""   # Same UUID as the SignalEvent that triggered this
    broker_ticket: int  = 0    # MT5 order ticket number (unique per broker)

    # ─── ORDER DETAILS ───────────────────────────────────────────────────────
    symbol: str    = ""
    direction: Optional[Direction] = None
    volume: float  = 0.0      # Lot size (e.g. 0.10 = 10,000 units of gold)
    status: TradeStatus = TradeStatus.PENDING

    # ─── PRICE DETAILS ───────────────────────────────────────────────────────
    requested_price: float = 0.0   # Entry price from SignalEvent
    fill_price: float      = 0.0   # Actual fill price from broker (may differ)
    stop_loss: float       = 0.0   # SL price set on broker
    take_profit: float     = 0.0   # TP price set on broker
    slippage_pips: float   = 0.0   # (fill_price - requested_price) in pips
                                   # Positive = slipped against you, negative = improved

    # ─── COST TRACKING ───────────────────────────────────────────────────────
    # Every cost must be tracked for accurate performance measurement.
    spread_at_entry: float   = 0.0   # Spread when order was sent (pips)
    spread_cost_usd: float   = 0.0   # Spread cost converted to USD
    commission: float        = 0.0   # Broker commission (USD roundtrip)
    swap: float              = 0.0   # Overnight swap cost if held past rollover
    total_cost_usd: float    = 0.0   # sum of spread_cost + commission + swap

    # ─── EXIT DETAILS ────────────────────────────────────────────────────────
    close_price: float          = 0.0
    close_reason: str           = ""   # Use CloseReason enum values as strings
    gross_pnl: float            = 0.0  # PnL in USD before deducting costs
    net_pnl: float              = 0.0  # PnL after all costs (what you actually keep)
    max_adverse_excursion: float = 0.0  # Worst drawdown during the trade (pips)
    max_favorable_excursion: float = 0.0  # Best profit reached during the trade (pips)

    # ─── TIMESTAMPS ──────────────────────────────────────────────────────────
    open_time: datetime           = field(default_factory=datetime.utcnow)
    close_time: Optional[datetime] = None
    duration_minutes: float       = 0.0

    # ─── MARKET CONTEXT AT ENTRY ─────────────────────────────────────────────
    regime_at_entry: str  = ""   # Regime enum value string at signal time
    session_at_entry: str = ""   # Session enum value string at order time
    score_at_entry: float = 0.0  # Confluence score from SignalEvent
    strategy: str         = ""   # Which alpha strategy generated the signal
    atr_at_entry: float   = 0.0  # ATR value when signal was generated

    # ─── CALCULATIONS ────────────────────────────────────────────────────────

    def calculate_costs(self) -> None:
        """
        Calculate total_cost_usd from components.
        Call this after filling in spread_cost_usd, commission, swap.
        """
        self.total_cost_usd = self.spread_cost_usd + self.commission + self.swap

    def calculate_net_pnl(self) -> None:
        """
        Net PnL = gross PnL minus all trading costs.
        Call this after close_price and gross_pnl are set.
        """
        self.calculate_costs()
        self.net_pnl = self.gross_pnl - self.total_cost_usd

    def calculate_duration(self) -> None:
        """Calculate trade duration in minutes once close_time is set."""
        if self.open_time and self.close_time:
            delta = self.close_time - self.open_time
            self.duration_minutes = round(delta.total_seconds() / 60, 1)

    def calculate_slippage(self) -> None:
        """
        Calculate slippage in pips.
        For XAUUSD: 1 pip = 0.10.
        Positive slippage = filled worse than requested (bad).
        Negative slippage = filled better than requested (rare but good).
        """
        if self.fill_price and self.requested_price:
            diff = self.fill_price - self.requested_price
            if self.direction == Direction.SELL:
                diff = -diff  # For SELL, higher fill = slippage against you
            self.slippage_pips = round(diff / 0.10, 1)

    def is_profitable(self) -> bool:
        """True if net PnL > 0."""
        return self.net_pnl > 0

    def pnl_pips(self) -> float:
        """
        Return PnL in pips (useful for normalized comparisons across lot sizes).
        XAUUSD: 1 pip = 0.10 price movement.
        """
        if not self.fill_price or not self.close_price:
            return 0.0
        diff = self.close_price - self.fill_price
        if self.direction == Direction.SELL:
            diff = -diff
        return round(diff / 0.10, 1)

    def __repr__(self) -> str:
        direction_str = self.direction.value if self.direction else "NONE"
        return (
            f"Trade({self.symbol} {direction_str} {self.volume}lot "
            f"@ {self.fill_price:.2f} → {self.close_price:.2f} "
            f"Net:{self.net_pnl:+.2f} USD Status:{self.status.value} "
            f"Reason:{self.close_reason})"
        )
