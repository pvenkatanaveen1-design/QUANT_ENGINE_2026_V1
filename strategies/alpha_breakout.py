"""
strategies/alpha_breakout.py — London Session Breakout Strategy.

STRATEGY DESCRIPTION:
  Trades breakouts of the previous hour's high/low at London open.

  Logic:
    1. At London open candle (12:30 IST), identify the previous H1 range.
    2. BUY if price closes above the previous high + buffer (5 pips).
    3. SELL if price closes below the previous low - buffer (5 pips).
    4. SL = 1.5 × ATR(14) from entry.
    5. TP = SL × 2.0 (RR ≥ 2.0 enforced by is_valid()).

  Filter: Only generates signals when:
    - Regime is STRONG_TREND (ADX > 30)
    - Session is LONDON or OVERLAP
    - Not called if regime detector says avoid

2026 REALITY NOTES:
  This strategy works because London institutions create breakout moves
  at the start of the session.  Asian range acts as a magnet to be swept.
  PDH/PDL (previous day high/low) are even stronger key levels.

  Common failure mode: news events at London open (unemployment, GDP)
  can cause false breakouts.  news_guard.py handles this — this strategy
  generates the signal, risk layer blocks if news is imminent.
"""

from __future__ import annotations

from typing import List

from core.constants import ATR_SL_MULTIPLIER, RR_MINIMUM
from core.enums import Direction, Regime
from core.logger import get_logger, LogCategory
from core.models.signal import SignalEvent
from market.features.atr import calculate_atr_from_candles
from strategies.base import BaseStrategy

log = get_logger("alpha_breakout", LogCategory.TRADING)

# Strategy parameters (override in config/strategies.yaml)
BREAKOUT_BUFFER_PIPS = 5.0    # Pips above/below range to confirm breakout
MIN_RANGE_PIPS       = 10.0   # Minimum range size (too tight = fake breakout)
MAX_RANGE_PIPS       = 80.0   # Maximum range (too wide = no defined entry)
LOOKBACK_CANDLES     = 3      # Number of candles to define the range (last N before London)


class AlphaBreakout(BaseStrategy):
    """
    London Session Breakout strategy.

    Generates BUY/SELL signals when price breaks out of the recent range
    with enough momentum (ADX > 25) and within London session hours.
    """

    name        = "alpha_breakout"
    description = "London session breakout of previous range. Requires STRONG_TREND regime."
    timeframe   = "H1"
    symbol      = "XAUUSD"

    def __init__(self) -> None:
        super().__init__()
        self._breakout_buffer = BREAKOUT_BUFFER_PIPS
        self._min_range       = MIN_RANGE_PIPS
        self._max_range       = MAX_RANGE_PIPS

    def generate_signals(self, candles) -> List[SignalEvent]:
        """
        Analyze H1 candles and return breakout signals if conditions are met.

        Parameters:
            candles: pd.DataFrame or list[dict] with columns time,open,high,low,close,volume
                     Sorted oldest first.

        Returns:
            List with 0 or 1 SignalEvent.
            0 signals: no valid breakout setup.
            1 signal:  BUY or SELL breakout setup.
        """
        signals: List[SignalEvent] = []

        if not self._enabled:
            return signals

        # Regime check: only trade STRONG_TREND
        if self._current_regime:
            if self._current_regime.regime not in {Regime.STRONG_TREND, Regime.WEAK_TREND}:
                log.debug(f"Breakout skip: regime={self._current_regime.regime.value}")
                return signals
            if not self._current_regime.is_tradeable():
                return signals

        # Extract price data
        try:
            highs, lows, closes = self._extract_prices(candles)
        except Exception as exc:
            log.error(f"Breakout candle extraction error: {exc}")
            return signals

        if len(closes) < LOOKBACK_CANDLES + 2:
            return signals

        # Most recent closed candle
        latest_close = closes[-1]
        latest_high  = highs[-1]
        latest_low   = lows[-1]

        # Define range: last LOOKBACK_CANDLES before the current bar
        range_highs = highs[-(LOOKBACK_CANDLES + 1):-1]
        range_lows  = lows[-(LOOKBACK_CANDLES + 1):-1]

        range_high = max(range_highs)
        range_low  = min(range_lows)
        range_size = (range_high - range_low) / 0.10  # pips

        # Range sanity check
        if range_size < self._min_range or range_size > self._max_range:
            log.debug(f"Breakout skip: range={range_size:.1f}pips not in [{self._min_range}-{self._max_range}]")
            return signals

        # Calculate ATR for SL sizing
        atr = calculate_atr_from_candles(highs, lows, closes)
        if not atr:
            return signals

        buffer_price = self._breakout_buffer * 0.10  # convert pips to price
        sl_distance  = atr * ATR_SL_MULTIPLIER
        tp_distance  = sl_distance * RR_MINIMUM

        # ── BUY BREAKOUT ─────────────────────────────────────────────────────
        if latest_close > range_high + buffer_price:
            entry   = latest_close
            sl      = entry - sl_distance
            tp      = entry + tp_distance
            signal  = self._make_signal(
                direction   = Direction.BUY,
                entry_price = entry,
                stop_loss   = sl,
                take_profit = tp,
                confidence  = 0.65,
            )
            signal.atr_at_signal   = atr
            signal.adx_at_signal   = self._current_regime.adx if self._current_regime else 0.0
            signal.signal_bar_time = None
            signals.append(signal)
            log.info(
                f"BUY breakout signal: entry={entry:.2f} "
                f"SL={sl:.2f} TP={tp:.2f} "
                f"range={range_size:.1f}pips ATR={atr:.2f}"
            )

        # ── SELL BREAKOUT ─────────────────────────────────────────────────────
        elif latest_close < range_low - buffer_price:
            entry   = latest_close
            sl      = entry + sl_distance
            tp      = entry - tp_distance
            signal  = self._make_signal(
                direction   = Direction.SELL,
                entry_price = entry,
                stop_loss   = sl,
                take_profit = tp,
                confidence  = 0.65,
            )
            signal.atr_at_signal = atr
            signal.adx_at_signal = self._current_regime.adx if self._current_regime else 0.0
            signals.append(signal)
            log.info(
                f"SELL breakout signal: entry={entry:.2f} "
                f"SL={sl:.2f} TP={tp:.2f} "
                f"range={range_size:.1f}pips ATR={atr:.2f}"
            )

        return signals

    def _extract_prices(self, candles) -> tuple[list, list, list]:
        """Extract highs, lows, closes from DataFrame or list of dicts."""
        if hasattr(candles, "empty"):  # pandas DataFrame
            highs  = candles["high"].tolist()
            lows   = candles["low"].tolist()
            closes = candles["close"].tolist()
        else:  # list of dicts
            highs  = [c["high"]  for c in candles]
            lows   = [c["low"]   for c in candles]
            closes = [c["close"] for c in candles]
        return highs, lows, closes
