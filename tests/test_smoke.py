from __future__ import annotations

from core.constants import CHANNEL_SIGNALS_RAW, CHANNEL_TICKS_PATTERN
from regime.classifiers.ensemble_regime import EnsembleRegimeClassifier
from risk.shield import Shield
from schemas.tick import Tick


def test_constants_present() -> None:
    assert "ticks" in CHANNEL_TICKS_PATTERN
    assert CHANNEL_SIGNALS_RAW


def test_tick_roundtrip() -> None:
    import datetime as dt

    t = Tick(symbol="EURUSD", bid=1.0, ask=1.0002, time_utc=dt.datetime.now(dt.timezone.utc))
    t2 = Tick.from_dict(t.to_dict())
    assert t2.symbol == t.symbol


def test_shield_loads() -> None:
    shield = Shield()
    assert "max_total_drawdown_pct" in shield.limits


def test_ensemble_runs_minimal() -> None:
    highs = [float(i) / 100000.0 + 1.0 for i in range(400)]
    lows = [h - 0.00025 for h in highs]
    closes = [h - 0.0001 for h in highs]
    out = EnsembleRegimeClassifier().classify(highs, lows, closes)
    assert out.label.value
