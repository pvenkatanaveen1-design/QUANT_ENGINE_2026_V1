from __future__ import annotations


def adx_latest(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> tuple[float | None, float | None]:
    """Return (ADX, +DI − −DI). Wilder smoothed approximation."""
    n = len(closes)
    if period < 2 or n < period * 2:
        return None, None

    tr_list: list[float] = []
    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []

    for i in range(1, n):
        high_i, low_i, close_prev = highs[i], lows[i], closes[i - 1]
        prev_high_i, prev_low_i = highs[i - 1], lows[i - 1]

        tr = max(high_i - low_i, abs(high_i - close_prev), abs(low_i - close_prev))

        up_move = high_i - prev_high_i
        down_move = prev_low_i - low_i

        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0

        tr_list.append(tr)
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    idx0 = period - 1

    atr = sum(tr_list[: idx0 + 1]) / period
    plus_di_s = sum(plus_dm_list[: idx0 + 1]) / period
    minus_di_s = sum(minus_dm_list[: idx0 + 1]) / period

    adx_val: float | None = None
    di_spread: float | None = None

    for k in range(idx0 + 1, len(tr_list)):
        tr_k = tr_list[k]
        pdm_k = plus_dm_list[k]
        mdm_k = minus_dm_list[k]

        atr = ((period - 1) * atr + tr_k) / period
        plus_di_s = ((period - 1) * plus_di_s + pdm_k) / period
        minus_di_s = ((period - 1) * minus_di_s + mdm_k) / period

        plus_di = 100.0 * plus_di_s / atr if atr else 0.0
        minus_di = 100.0 * minus_di_s / atr if atr else 0.0
        denom = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / denom if denom else 0.0

        di_spread = plus_di - minus_di

        if adx_val is None:
            adx_val = dx
        else:
            adx_val = ((period - 1) * adx_val + dx) / period

    if adx_val is None or di_spread is None:
        return None, None
    return adx_val, di_spread
