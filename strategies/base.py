"""
strategies/base.py — Base class for all alpha signal generation strategies.

WHY THIS FILE EXISTS
--------------------
All strategies (alpha_breakout, alpha_sweep, alpha_pullback) share the same
interface.  The backtester, strategy selector, and scoring engine call
the same method regardless of which strategy is being used.

This eliminates: "does this strategy use generate() or run() or compute()?"

STRATEGY INTERFACE:
  Every strategy must implement:
    generate_signals(candles) → list[SignalEvent]

  Optionally override:
    on_candle_closed(candle)   → called live (from EventBus)
    on_regime_changed(regime)  → react to regime changes

HOW STRATEGIES STAY BACKTEST-COMPATIBLE:
  In LIVE mode:
    The regime_detector subscribes to CANDLE_CLOSED.
    It then calls strategy.generate_signals(candles).
    The strategy returns signals → scoring engine → risk → execution.

  In BACKTEST mode:
    The backtester feeds candles one at a time.
    It calls strategy.generate_signals(candles_up_to_this_bar).
    The strategy returns signals → backtester applies spread/slippage.

  The strategy itself never knows if it is live or in backtest.
  It always sees: "here are the candles up to now, what do you think?"

WHAT STRATEGIES DO NOT DO:
  - Risk checks (that's risk/shield.py and risk/cost_guard.py)
  - Position sizing (that's risk/position_sizer.py)
  - Order submission (that's execution/router.py)
  - State management (that's core/state_store.py)

  Strategies ONLY look at price data and return SignalEvent objects.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, TYPE_CHECKING

from core.enums import Direction, Regime, Session
from core.logger import get_logger, LogCategory
from core.models.signal import SignalEvent

if TYPE_CHECKING:
    from core.models.regime import RegimeState

log = get_logger("strategy_base", LogCategory.TRADING)


class BaseStrategy(ABC):
    """
    Abstract base for all signal generation strategies.

    Subclass this and implement generate_signals().
    The system will call generate_signals() on every H1 candle close.

    Example:
        class MyStrategy(BaseStrategy):
            name = "my_strategy"

            def generate_signals(self, candles) -> List[SignalEvent]:
                # Look at candles, find patterns
                # Return list of SignalEvent objects (empty if no signal)
                return []
    """

    name: str = "unnamed_strategy"
    description: str = ""
    timeframe: str = "H1"
    symbol: str = "XAUUSD"

    def __init__(self) -> None:
        self._enabled = True
        self._signal_count = 0
        self._current_regime: Optional[RegimeState] = None
        log.debug(f"Strategy '{self.name}' initialized")

    @abstractmethod
    def generate_signals(self, candles) -> List[SignalEvent]:
        """
        Analyze candle data and return any trade signals.

        Parameters:
            candles: Either a pandas DataFrame (live) or list of dicts (backtest).
                     Columns/keys: time, open, high, low, close, volume
                     Sorted oldest first (index 0 = oldest bar).

        Returns:
            list[SignalEvent]: Empty list = no signal.
                               One or more SignalEvent objects = potential trades.
                               Each SignalEvent must pass is_valid() before use.

        This method must be PURE — same inputs always produce same outputs.
        No random numbers.  No time.now() calls inside signal logic.
        (Use candle timestamps, not wall clock time, for signal timing.)
        """
        ...

    def on_regime_changed(self, regime: RegimeState) -> None:
        """
        Called when regime_detector publishes a new regime.
        Override to adapt strategy parameters to current regime.
        Default: just stores the regime for use in generate_signals().
        """
        self._current_regime = regime
        log.debug(f"Strategy '{self.name}' received regime: {regime.regime.value}")

    def on_candle_closed(self, candle: dict) -> Optional[List[SignalEvent]]:
        """
        Called from EventBus handler on each H1 candle close.
        Default: fetches recent candles from hub and calls generate_signals().
        Override for custom live behavior.
        """
        if not self._enabled:
            return []
        try:
            from systems.data.market_data_hub import hub
            candles = hub.get_candles(self.symbol, self.timeframe, n=200)
            signals = self.generate_signals(candles)
            self._signal_count += len(signals)
            return signals
        except Exception as exc:
            log.error(f"Strategy '{self.name}' on_candle_closed error: {exc}", exc_info=True)
            return []

    def enable(self) -> None:
        """Enable this strategy.  Call from config editor in dashboard."""
        self._enabled = True
        log.info(f"Strategy '{self.name}' enabled")

    def disable(self) -> None:
        """Disable this strategy.  No signals will be generated."""
        self._enabled = False
        log.info(f"Strategy '{self.name}' disabled")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def signal_count(self) -> int:
        """Total signals generated since startup."""
        return self._signal_count

    def _make_signal(
        self,
        direction:    Direction,
        entry_price:  float,
        stop_loss:    float,
        take_profit:  float,
        confidence:   float = 0.5,
    ) -> SignalEvent:
        """
        Helper to build a SignalEvent with common fields pre-filled.
        Subclasses call this instead of constructing SignalEvent manually.
        """
        sl_pips = abs(entry_price - stop_loss) / 0.10
        tp_pips = abs(entry_price - take_profit) / 0.10
        rr = round(tp_pips / sl_pips, 2) if sl_pips > 0 else 0.0

        regime     = self._current_regime
        regime_val = regime.regime if regime else None
        session    = regime.session if regime else None

        signal = SignalEvent(
            symbol        = self.symbol,
            timeframe     = self.timeframe,
            direction     = direction,
            strategy      = self.name,
            entry_price   = entry_price,
            stop_loss     = stop_loss,
            take_profit   = take_profit,
            sl_pips       = sl_pips,
            tp_pips       = tp_pips,
            rr_ratio      = rr,
            regime        = regime_val,
            session       = session,
            confidence    = confidence,
        )
        return signal

    def get_info(self) -> dict:
        """Return strategy metadata for dashboard Strategy Builder page."""
        return {
            "name":         self.name,
            "description":  self.description,
            "timeframe":    self.timeframe,
            "symbol":       self.symbol,
            "enabled":      self._enabled,
            "signal_count": self._signal_count,
        }
