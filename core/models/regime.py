"""
core/models/regime.py — RegimeState dataclass.

WHY THIS FILE EXISTS
--------------------
The regime detector (regime/detector.py) publishes its output as a RegimeState.
Strategy selectors and risk engines READ this object.
By using a typed dataclass, there are no "is it TREND or TRENDING?" confusion bugs.

2026 FOREX REALITY NOTES
-------------------------
- confidence threshold: is_tradeable() requires confidence >= 0.70.
  Regime detection on XAUUSD is reliable 70–80% of the time.
  The remaining 20–30% is transition periods (regime flip) where the
  indicators disagree.  Below 0.70 confidence = ambiguous = skip.

- bars_in_regime: A regime that just started (1-2 bars) is unstable.
  Many false regime signals flip back within 2-3 candles.
  The strategy selector should require bars_in_regime >= 3 before trading.

- atr_percentile: This is the ATR value ranked vs the last 20 periods.
  0 = lowest volatility in 20 bars.  100 = highest.
  HIGH_VOL regime triggers when atr_percentile > 80.
  Trading in extreme volatility (atr_percentile > 90) has wide spreads
  and high slippage — regime detector marks this as HIGH_VOL, not tradeable.

- allows_strategy mapping is conservative.  In live markets:
  alpha_sweep (liquidity sweeps) also works in WEAK_TREND — a sweep of
  the prior swing low often forms in weak trending conditions.
  alpha_pullback works best in STRONG_TREND but can be attempted in WEAK_TREND.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from core.enums import Regime, Session


@dataclass
class RegimeState:
    """
    Current market regime output from regime/detector.py.
    Published via EventBus.  Read by strategy selector and risk engine.
    """

    # ─── WHAT SYMBOL THIS IS FOR ─────────────────────────────────────────────
    symbol: str = ""
    timeframe: str = "H1"         # Timeframe the regime was detected on

    # ─── CURRENT REGIME ──────────────────────────────────────────────────────
    regime: Regime = Regime.UNKNOWN

    # ─── INDICATOR VALUES THAT DETERMINED THE REGIME ─────────────────────────
    # Storing these lets you audit WHY the regime was classified.
    adx: float          = 0.0   # ADX(14) value — > 30 strong trend, < 20 range
    atr: float          = 0.0   # ATR(14) value in price units (e.g. 2.50 for XAUUSD)
    atr_percentile: float = 0.0  # ATR rank vs 20-bar lookback — 0=lowest, 100=highest
    ema_fast: float     = 0.0   # Fast EMA (e.g. 20-period) for trend direction
    ema_slow: float     = 0.0   # Slow EMA (e.g. 50-period) for trend direction
    trend_direction: str = ""   # "BULLISH", "BEARISH", "NEUTRAL" based on EMA alignment

    # ─── SESSION ─────────────────────────────────────────────────────────────
    session: Session = Session.OFF

    # ─── CONFIDENCE ──────────────────────────────────────────────────────────
    # How certain the detector is about this regime classification.
    # Calculated from: indicator agreement, candle structure, volume (if available).
    # Only trade when confidence >= 0.70.
    confidence: float = 0.0

    # ─── REGIME HISTORY ──────────────────────────────────────────────────────
    previous_regime: Optional[Regime] = None
    regime_changed: bool  = False   # True if regime flipped on this bar
    bars_in_regime: int   = 0       # How many consecutive bars in current regime
                                    # Require >= 3 before trusting the regime

    # ─── EXTENDED MULTI-FACTOR OUTPUT (V2) ───────────────────────────────────
    regime_label: str = "TRANSITION"           # 12-regime label for UI + strategy map
    probabilities: dict[str, float] = field(default_factory=dict)
    transition_state: str = "STABLE"
    structure_label: str = "UNKNOWN"
    session_label: str = "OFF"
    rsi: float = 0.0
    volume_signal: str = "UNKNOWN"
    candles_used: int = 0
    lookback_years: float = 1.0
    allowed_strategies: list[str] = field(default_factory=list)
    mapping_reason: str = ""

    # ─── TIMESTAMP ───────────────────────────────────────────────────────────
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # ─── TRADABILITY CHECKS ──────────────────────────────────────────────────

    def is_tradeable(self) -> bool:
        """
        Returns True only when conditions are right to consider trading.

        Blocks:
        - NEWS_CHAOS: spreads unreliable, fills dangerous
        - UNKNOWN: not enough data yet (startup or gap in feed)
        - HIGH_VOL: extreme ATR spike — too much slippage risk
        - confidence < 0.70: indicators disagree → ambiguous regime
        - Session.OFF: no institutional volume → choppy, trap-prone
        - bars_in_regime < 3: regime just started, may be a false signal
        """
        untradeable_regimes = {Regime.NEWS_CHAOS, Regime.UNKNOWN, Regime.HIGH_VOL}
        if self.regime in untradeable_regimes:
            return False
        if self.confidence < 0.70:
            return False
        if self.session == Session.OFF:
            return False
        if self.bars_in_regime < 3:
            return False
        return True

    def allows_strategy(self, strategy_name: str) -> bool:
        """
        Check if a specific strategy is valid in the current regime.

        Mapping (based on 2026 backtesting reality for XAUUSD):
          alpha_sweep    → RANGE, WEAK_TREND
            (sweeps false breakout lows/highs — works in choppy/ranging conditions)
          alpha_breakout → STRONG_TREND only
            (true breakouts require strong directional momentum)
          alpha_pullback → STRONG_TREND, WEAK_TREND
            (pullbacks valid in any trending regime)
        """
        strategy_regime_map: dict[str, list[Regime]] = {
            "alpha_sweep":    [Regime.RANGE, Regime.WEAK_TREND],
            "alpha_breakout": [Regime.STRONG_TREND],
            "alpha_pullback": [Regime.STRONG_TREND, Regime.WEAK_TREND],
        }
        strategy_regime12_map: dict[str, list[str]] = {
            "alpha_breakout": ["TREND_HIGH_VOL", "BREAKOUT_EXPANSION", "PULLBACK_CONTINUATION"],
            "alpha_pullback": ["TREND_LOW_VOL", "TREND_HIGH_VOL", "PULLBACK_CONTINUATION"],
            "alpha_sweep": ["RANGE_LOW_VOL", "RANGE_HIGH_VOL", "LIQUIDITY_SWEEP", "NY_REVERSAL"],
        }
        if self.regime_label:
            allowed12 = strategy_regime12_map.get(strategy_name, [])
            if allowed12:
                return self.regime_label in allowed12
        allowed = strategy_regime_map.get(strategy_name, [])
        return self.regime in allowed

    def is_trending(self) -> bool:
        """True if regime is STRONG_TREND or WEAK_TREND."""
        return self.regime in {Regime.STRONG_TREND, Regime.WEAK_TREND}

    def is_ranging(self) -> bool:
        """True if regime is RANGE."""
        return self.regime == Regime.RANGE

    def __repr__(self) -> str:
        return (
            f"Regime({self.symbol} {self.regime.value} "
            f"ADX:{self.adx:.1f} ATR:{self.atr:.2f} "
            f"ATR%:{self.atr_percentile:.0f} "
            f"Conf:{self.confidence:.0%} "
            f"Session:{self.session.value} "
            f"Bars:{self.bars_in_regime})"
        )
