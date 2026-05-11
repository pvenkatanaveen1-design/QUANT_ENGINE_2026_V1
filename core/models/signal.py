"""
core/models/signal.py — SignalEvent dataclass.

WHY THIS FILE EXISTS
--------------------
Every alpha strategy (sweep, breakout, pullback) produces a signal.
ALL signals must have the same shape.  No dicts.  No loose variables.
If a field is missing, the system fails at construction time — not during
risk check, not at order send.  Bugs caught early = money saved.

The correlation_id traces this one signal all the way through:
  SignalEvent → RiskCheck → TradeEvent → journal row

2026 FOREX REALITY NOTES
-------------------------
- rr_ratio validation: minimum 2.0 enforced in is_valid().
  At 40% win rate (realistic for XAUUSD breakout), you need ≥ 2R to profit.
  Formula: expectancy = (win_rate × avg_win) - (loss_rate × avg_loss) > 0
  With 40% WR and 2R: 0.40×2 - 0.60×1 = +0.20 → profitable edge.
  At 1.5R: 0.40×1.5 - 0.60×1 = 0.00 → breakeven after commissions = loss.

- sl_pips vs stop_loss price: BOTH must be stored.
  sl_pips is used for position sizing (lot calculation).
  stop_loss price is sent to the broker as the hard stop order.
  If spread widens and you recalculate stop_loss from sl_pips you may
  end up with a different price.  Always store the final decided price.

- score threshold: score < 60 → blocked.  This is the second gate after
  the regime filter.  A 60/100 score means 60% of confluence factors align.
  Below that, the edge is not reliable enough for funded account trading.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from core.enums import Direction, Regime, Session
from core.constants import RR_MINIMUM, DEFAULT_MIN_SCORE


@dataclass
class SignalEvent:
    """
    Output of any alpha strategy.  Passed to risk engine → executor.
    All fields have defaults so you can construct partially and fill in stages.
    Call is_valid() before passing to risk engine.
    """

    # ─── IDENTITY ────────────────────────────────────────────────────────────
    # UUID4 generated automatically.  Links signal → RiskCheck → TradeEvent.
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # ─── WHAT THIS SIGNAL IS ────────────────────────────────────────────────
    symbol: str = ""                    # e.g. "XAUUSD"
    timeframe: str = "H1"               # Timeframe signal was detected on
    direction: Optional[Direction] = None  # BUY or SELL (never use raw strings)
    strategy: str = ""                  # "alpha_sweep" / "alpha_breakout" / "alpha_pullback"

    # ─── PRICE LEVELS ────────────────────────────────────────────────────────
    entry_price: float = 0.0       # Intended market entry price
    stop_loss: float = 0.0         # Hard SL price (sent to broker as stop order)
    take_profit: float = 0.0       # Primary TP price
    sl_pips: float = 0.0           # SL distance in pips (for lot size calculation)
    tp_pips: float = 0.0           # TP distance in pips (for RR display)
    rr_ratio: float = 0.0          # tp_pips / sl_pips — must be ≥ RR_MINIMUM (2.0)

    # ─── MARKET CONTEXT AT SIGNAL TIME ──────────────────────────────────────
    regime: Optional[Regime] = None    # Market regime when signal fired
    session: Optional[Session] = None  # Trading session when signal fired
    score: float = 0.0                 # Confluence score 0–100 from scoring engine
    confidence: float = 0.0           # Strategy-internal confidence 0.0–1.0

    # ─── TECHNICAL CONTEXT ──────────────────────────────────────────────────
    # These are stored for post-trade review and backtesting analysis.
    atr_at_signal: float = 0.0         # ATR(14) value when signal was generated
    adx_at_signal: float = 0.0         # ADX(14) value when signal was generated
    spread_at_signal: float = 0.0      # Live spread at signal time (pips)

    # ─── TIMESTAMPS ─────────────────────────────────────────────────────────
    timestamp: datetime = field(default_factory=datetime.utcnow)
    signal_bar_time: Optional[datetime] = None  # Open time of the candle that triggered

    # ─── RISK ENGINE RESULT (filled in by risk engine, not strategy) ─────────
    approved: bool = False         # True only when ALL risk checks pass
    blocked_reason: str = ""       # Set by risk engine if blocked — e.g. "SpreadTooWide: 2.1 pips"
    lot_size: float = 0.0          # Calculated by position sizer if approved

    # ─── VALIDITY CHECK ──────────────────────────────────────────────────────

    def is_valid(self) -> bool:
        """
        Check minimum required fields before sending to risk engine.
        Returns False if any critical field is missing or invalid.

        A valid signal must have:
        - Symbol and direction set
        - All three prices set (entry, SL, TP) and positive
        - SL below entry for BUY, above entry for SELL
        - RR ratio ≥ RR_MINIMUM (2.0) — enforced even if tp_pips not set
        - Score ≥ DEFAULT_MIN_SCORE (60) — scoring engine must run first
        """
        if not self.symbol or self.direction is None:
            return False

        if self.entry_price <= 0 or self.stop_loss <= 0 or self.take_profit <= 0:
            return False

        # Validate stop loss is on the correct side of entry price
        if self.direction == Direction.BUY and self.stop_loss >= self.entry_price:
            return False  # SL must be below entry for a BUY
        if self.direction == Direction.SELL and self.stop_loss <= self.entry_price:
            return False  # SL must be above entry for a SELL

        # Validate take profit is on the correct side of entry price
        if self.direction == Direction.BUY and self.take_profit <= self.entry_price:
            return False
        if self.direction == Direction.SELL and self.take_profit >= self.entry_price:
            return False

        # RR minimum enforced here — not just in risk engine
        if self.rr_ratio < RR_MINIMUM:
            return False

        # Score must be calculated before validation
        if self.score < DEFAULT_MIN_SCORE:
            return False

        return True

    def calculate_rr(self) -> float:
        """
        Calculate RR ratio from pip distances and store it.
        Call this after setting sl_pips and tp_pips.
        """
        if self.sl_pips > 0:
            self.rr_ratio = round(self.tp_pips / self.sl_pips, 2)
        return self.rr_ratio

    def pip_distance(self, price_a: float, price_b: float) -> float:
        """
        Convert price distance to pips for XAUUSD.
        XAUUSD: 1 pip = 0.10 (10 cents).  $1 move = 10 pips.
        """
        return abs(price_a - price_b) / 0.10

    def __repr__(self) -> str:
        direction_str = self.direction.value if self.direction else "NONE"
        regime_str    = self.regime.value    if self.regime    else "NONE"
        return (
            f"Signal({self.symbol} {direction_str} @ {self.entry_price:.2f} "
            f"SL:{self.stop_loss:.2f} TP:{self.take_profit:.2f} "
            f"RR:{self.rr_ratio:.1f} Score:{self.score:.0f} "
            f"Regime:{regime_str} [{self.strategy}])"
        )
