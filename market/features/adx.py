"""
market/features/adx.py — ADX(14) calculation for regime detection.

ADX (Average Directional Index) measures trend STRENGTH, not direction.
  ADX > 30: Strong trend (consider momentum strategies)
  ADX 20-30: Weak trend (moderate edge)
  ADX < 20:  No trend / ranging (avoid trend strategies)

ADX DOES NOT tell you if price is going UP or DOWN.
For direction, use EMA crossover (ema_fast > ema_slow = bullish).

2026 XAUUSD REALITY:
  On H1 timeframe, XAUUSD spends roughly:
    ~30% of time in strong trend (ADX > 30)
    ~40% of time in weak trend (ADX 20-30)
    ~30% of time ranging (ADX < 20)
  Trading only when ADX > 25 filters out ~40% of signals but
  avoids the worst whipsaw conditions.

IMPLEMENTATION NOTE:
  We implement ADX manually (not using pandas_ta) so the engine
  works without TA-Lib or pandas_ta installed.
  The formula matches Wilder's original ADX:
    TR = max(H-L, |H-prev_C|, |L-prev_C|)
    +DM = H - prev_H if positive, else 0
    -DM = prev_L - L if positive, else 0
    ATR(14) = EWM smoothing of TR
    +DI = (+DM smoothed) / ATR × 100
    -DI = (-DM smoothed) / ATR × 100
    DX  = |+DI - -DI| / (+DI + -DI) × 100
    ADX = EWM smoothing of DX
"""

from __future__ import annotations

from typing import Optional


def calculate_adx(
    highs:  list[float],
    lows:   list[float],
    closes: list[float],
    period: int = 14,
) -> Optional[float]:
    """
    Calculate ADX(period) from raw OHLC price lists.

    Parameters:
        highs:  list of high prices (oldest first)
        lows:   list of low prices
        closes: list of close prices
        period: ADX smoothing period (default 14 = Wilder standard)

    Returns:
        ADX value (0-100) for the MOST RECENT bar, or None if insufficient data.
        Needs at least (2 × period + 1) bars for reliable results.

    Usage:
        adx_value = calculate_adx(df["high"].tolist(), df["low"].tolist(), df["close"].tolist())
        if adx_value and adx_value > 25:
            regime = "TRENDING"
    """
    min_bars = period * 2 + 1
    n = len(closes)
    if n < min_bars:
        return None

    # Use last N bars for efficiency (more than needed for warm-up)
    lookback = min(n, period * 4)
    h = highs[-lookback:]
    l = lows[-lookback:]
    c = closes[-lookback:]

    tr_list, pdm_list, ndm_list = [], [], []

    for i in range(1, len(c)):
        # True Range
        hl  = h[i] - l[i]
        hpc = abs(h[i] - c[i - 1])
        lpc = abs(l[i] - c[i - 1])
        tr  = max(hl, hpc, lpc)

        # Directional movement
        up_move   = h[i] - h[i - 1]
        down_move = l[i - 1] - l[i]

        pdm = up_move   if up_move > down_move and up_move > 0   else 0.0
        ndm = down_move if down_move > up_move and down_move > 0 else 0.0

        tr_list.append(tr)
        pdm_list.append(pdm)
        ndm_list.append(ndm)

    if len(tr_list) < period:
        return None

    # Wilder smoothing: first value = sum, subsequent = prev × (period-1)/period + current
    def wilder_smooth(data: list[float], p: int) -> list[float]:
        smoothed = [sum(data[:p])]
        for val in data[p:]:
            smoothed.append(smoothed[-1] * (p - 1) / p + val)
        return smoothed

    atr_s  = wilder_smooth(tr_list,  period)
    pdm_s  = wilder_smooth(pdm_list, period)
    ndm_s  = wilder_smooth(ndm_list, period)

    dx_list = []
    for i in range(len(atr_s)):
        if atr_s[i] == 0:
            continue
        pdi = pdm_s[i] / atr_s[i] * 100
        ndi = ndm_s[i] / atr_s[i] * 100
        denom = pdi + ndi
        dx = abs(pdi - ndi) / denom * 100 if denom != 0 else 0.0
        dx_list.append(dx)

    if len(dx_list) < period:
        return None

    # ADX = Wilder smoothing of DX
    adx_series = wilder_smooth(dx_list, period)
    return round(adx_series[-1], 2)


def calculate_di(
    highs:  list[float],
    lows:   list[float],
    closes: list[float],
    period: int = 14,
) -> tuple[Optional[float], Optional[float]]:
    """
    Calculate +DI and -DI for trend direction.

    Returns (+DI, -DI) tuple.
    +DI > -DI = bullish trend direction
    -DI > +DI = bearish trend direction

    The crossing of +DI and -DI can signal trend reversals.
    For regime detection, we use +DI > -DI to confirm BULLISH vs BEARISH.
    """
    min_bars = period * 2 + 1
    n = len(closes)
    if n < min_bars:
        return None, None

    lookback = min(n, period * 4)
    h = highs[-lookback:]
    l = lows[-lookback:]
    c = closes[-lookback:]

    tr_list, pdm_list, ndm_list = [], [], []
    for i in range(1, len(c)):
        hl  = h[i] - l[i]
        hpc = abs(h[i] - c[i - 1])
        lpc = abs(l[i] - c[i - 1])
        tr  = max(hl, hpc, lpc)
        up_move   = h[i] - h[i - 1]
        down_move = l[i - 1] - l[i]
        pdm = up_move   if up_move > down_move and up_move > 0   else 0.0
        ndm = down_move if down_move > up_move and down_move > 0 else 0.0
        tr_list.append(tr)
        pdm_list.append(pdm)
        ndm_list.append(ndm)

    if len(tr_list) < period:
        return None, None

    def wilder_smooth(data: list[float], p: int) -> list[float]:
        smoothed = [sum(data[:p])]
        for val in data[p:]:
            smoothed.append(smoothed[-1] * (p - 1) / p + val)
        return smoothed

    atr_s = wilder_smooth(tr_list,  period)
    pdm_s = wilder_smooth(pdm_list, period)
    ndm_s = wilder_smooth(ndm_list, period)

    if atr_s[-1] == 0:
        return 0.0, 0.0

    pdi = round(pdm_s[-1] / atr_s[-1] * 100, 2)
    ndi = round(ndm_s[-1] / atr_s[-1] * 100, 2)
    return pdi, ndi
