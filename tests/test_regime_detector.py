"""
tests/test_regime_detector.py — Tests for systems/intelligence/regime_detector.py

Run: pytest tests/test_regime_detector.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from core.enums import Regime


def _make_trending_data(n=100):
    """Generate synthetic trending OHLCV data (ADX should be high)."""
    base = 2300.0
    highs, lows, closes = [], [], []
    for i in range(n):
        base += 2.0  # Strong uptrend
        highs.append(base + 1.0)
        lows.append(base - 0.5)
        closes.append(base + 0.5)
    return highs, lows, closes


def _make_ranging_data(n=100):
    """Generate synthetic ranging OHLCV data (ADX should be low)."""
    import math
    base = 2300.0
    highs, lows, closes = [], [], []
    for i in range(n):
        price = base + math.sin(i * 0.3) * 5.0  # Sideways oscillation
        highs.append(price + 1.0)
        lows.append(price - 1.0)
        closes.append(price)
    return highs, lows, closes


class TestRegimeClassification:
    def test_strong_trend_detected(self):
        from systems.intelligence.regime_detector import RegimeDetector
        from core.enums import Regime
        det = RegimeDetector()
        highs, lows, closes = _make_trending_data(100)
        regime, confidence = det._determine_regime(35.0, 2.0, 60.0, 2320.0, 2300.0)
        assert regime == Regime.STRONG_TREND
        assert confidence >= 0.70

    def test_ranging_detected(self):
        from systems.intelligence.regime_detector import RegimeDetector
        det = RegimeDetector()
        regime, confidence = det._determine_regime(15.0, 1.5, 40.0, 2310.0, 2308.0)
        assert regime == Regime.RANGE

    def test_weak_trend_zone(self):
        from systems.intelligence.regime_detector import RegimeDetector
        det = RegimeDetector()
        regime, confidence = det._determine_regime(23.0, 2.0, 50.0, 2310.0, 2305.0)
        assert regime == Regime.WEAK_TREND

    def test_high_vol_detected(self):
        from systems.intelligence.regime_detector import RegimeDetector
        det = RegimeDetector()
        # atr_percentile > 85 → HIGH_VOL
        regime, confidence = det._determine_regime(35.0, 5.0, 90.0, 2310.0, 2300.0)
        assert regime == Regime.HIGH_VOL


class TestATRCalculation:
    def test_atr_calculation_returns_float(self):
        from systems.intelligence.regime_detector import RegimeDetector
        det = RegimeDetector()
        highs, lows, closes = _make_trending_data(50)
        atr = det._calculate_atr(highs, lows, closes)
        assert atr is not None
        assert atr > 0

    def test_atr_insufficient_data_returns_none(self):
        from systems.intelligence.regime_detector import RegimeDetector
        det = RegimeDetector()
        atr = det._calculate_atr([1.0, 2.0], [0.5, 1.5], [0.8, 1.8])
        assert atr is None

    def test_ema_calculation(self):
        from systems.intelligence.regime_detector import RegimeDetector
        det = RegimeDetector()
        closes = list(range(100, 200))
        ema = det._calculate_ema(closes, 20)
        assert ema is not None
        # EMA should be close to the average of recent values
        assert 150 < ema < 200


class TestADXCalculation:
    def test_adx_trending_data(self):
        from market.features.adx import calculate_adx
        highs, lows, closes = _make_trending_data(100)
        adx = calculate_adx(highs, lows, closes)
        assert adx is not None
        assert adx > 20  # Trending data should have ADX > 20

    def test_adx_insufficient_returns_none(self):
        from market.features.adx import calculate_adx
        adx = calculate_adx([1.0], [0.9], [0.95])
        assert adx is None

    def test_di_direction(self):
        from market.features.adx import calculate_di
        highs, lows, closes = _make_trending_data(100)
        pdi, ndi = calculate_di(highs, lows, closes)
        # In uptrend, +DI should be > -DI
        assert pdi is not None
        assert pdi > ndi  # Uptrend: +DI dominates
