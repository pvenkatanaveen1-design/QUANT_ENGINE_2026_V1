from __future__ import annotations

from core.clock import utc_now
from schemas.regime import RegimeLabel, RegimeState
from schemas.signal import Signal, SignalStrength
from signals.liquidity import sweep_detector as sweep_det


def maybe_generate_signal(
    symbol: str,
    regime: RegimeState,
    last_mid: float,
    rng_high: float | None,
    rng_low: float | None,
) -> Signal | None:
    sweep_buy = regime.label == RegimeLabel.TREND and sweep_det.liquidity_sweep(
        "buy", last_mid, rng_high, buffer=0.0
    )
    sweep_sell = regime.label == RegimeLabel.TREND and sweep_det.liquidity_sweep(
        "sell", last_mid, rng_low, buffer=0.0
    )

    if sweep_buy:
        return Signal(
            symbol=symbol,
            direction="buy",
            rationale="trend + liquidity sweep high",
            strength=SignalStrength.MODERATE,
            time_utc=utc_now(),
            confluence_score=0.0,
        )
    if sweep_sell:
        return Signal(
            symbol=symbol,
            direction="sell",
            rationale="trend + liquidity sweep low",
            strength=SignalStrength.MODERATE,
            time_utc=utc_now(),
            confluence_score=0.0,
        )
    return None
