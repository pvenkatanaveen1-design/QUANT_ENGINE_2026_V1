"""Funded-account safe position sizing (reference lots - no MT5 orders placed here)."""

# We use UTC timestamps for Redis `sizing:last_update`.
from datetime import datetime, timezone

# Redis + settings stay centralized for beginners.
from core.bus import get_value, set_value
from core.config import load_config
from core.logger import get_logger


log = get_logger()

# Simplified $ pip value per 1.0 standard lot for risk math only (not live cash results).
# Future MT5 integration should replace these with broker-specific contract metadata.
PIP_VALUE_USD_PER_LOT = {
    "EURUSD": 10.0,
    "GBPUSD": 10.0,
    "USDJPY": 9.0,
    "XAUUSD": 1.0,
    "BTCUSD": 1.0,
}

SUPPORTED_SYMBOLS = frozenset(PIP_VALUE_USD_PER_LOT.keys())


def _normalize_symbol(symbol):
    """Return uppercase trimmed symbol or empty string."""
    if symbol is None:
        return ""
    return str(symbol).strip().upper()


def _parse_number(raw):
    """Parse Redis/config numbers safely (mirrors patterns used in risk/shield)."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def get_pip_value(symbol):
    """
    Return simplified USD pip value per standard lot for `symbol`.

    None means unsupported — callers should refuse sizing instead of guessing.
    """
    key = _normalize_symbol(symbol)
    if not key:
        return None
    return PIP_VALUE_USD_PER_LOT.get(key)


def calculate_risk_amount(balance, risk_percent):
    """
    Dollar risk budget from balance and risk percent.

    Example: balance 100_000 and 0.5% -> 500.0 USD notional risk envelope.
    """
    bal = _parse_number(balance)
    rp = _parse_number(risk_percent)
    if bal is None or bal <= 0:
        return None
    if rp is None or rp < 0:
        return None
    return bal * (rp / 100.0)


def _round_lot(symbol, lot_size):
    """Forex + gold -> 2 decimals; BTCUSD -> 3 decimals."""
    key = _normalize_symbol(symbol)
    if key == "BTCUSD":
        return round(float(lot_size), 3)
    return round(float(lot_size), 2)


def calculate_lot_size(balance, risk_percent, sl_pips, symbol):
    """
    Core sizing formula (simplified prop-style):

        lot_size = risk_amount / (sl_pips * pip_value_per_lot)

    Returns:
      (rounded_lot_or_None, detail_dict)

    detail_dict always includes helpful keys for logging; includes `error` when invalid.
    """
    key = _normalize_symbol(symbol)
    pip_val = get_pip_value(key)
    risk_amount = calculate_risk_amount(balance, risk_percent)
    sl = _parse_number(sl_pips)

    detail = {
        "symbol": key or symbol,
        "balance": balance,
        "risk_percent": risk_percent,
        "sl_pips": sl_pips,
        "pip_value": pip_val,
        "risk_amount": risk_amount,
    }

    if not key or pip_val is None:
        detail["error"] = "unsupported_or_missing_symbol"
        log.warning(f"position_sizer | unsupported symbol={symbol!r}")
        return None, detail

    if sl is None or sl <= 0:
        detail["error"] = "invalid_stop_loss_pips"
        log.warning(f"position_sizer | invalid SL pips={sl_pips!r}")
        return None, detail

    if risk_amount is None:
        detail["error"] = "invalid_balance_or_risk"
        log.warning(f"position_sizer | invalid balance/risk balance={balance!r} risk={risk_percent!r}")
        return None, detail

    denominator = sl * pip_val
    if denominator <= 0:
        detail["error"] = "zero_denominator"
        return None, detail

    raw_lot = risk_amount / denominator
    rounded = _round_lot(key, raw_lot)
    detail["lot_size_raw"] = raw_lot
    detail["lot_size"] = rounded
    return rounded, detail


def publish_position_size(symbol, risk_percent, sl_pips, balance=None):
    """
    Compute lot size and publish `sizing:*` snapshot to Redis.

    If `balance` is None, reads `account:balance` via `get_value`.

    If `risk_percent` is None, uses `DEFAULT_RISK_PER_TRADE` from config.

    Does not place orders — execution layers should consume these keys later.
    """
    cfg = load_config()
    if risk_percent is None:
        risk_percent = cfg["DEFAULT_RISK_PER_TRADE"]

    bal = balance
    if bal is None:
        bal = _parse_number(get_value("account:balance"))

    lot, detail = calculate_lot_size(bal, risk_percent, sl_pips, symbol)
    now_ts = datetime.now(timezone.utc).timestamp()

    payload = {
        "sizing:last_symbol": _normalize_symbol(symbol) or None,
        "sizing:last_balance": bal,
        "sizing:last_risk_percent": _parse_number(risk_percent),
        "sizing:last_sl_pips": _parse_number(sl_pips),
        "sizing:last_lot_size": lot,
        "sizing:last_update": now_ts,
    }

    for key, val in payload.items():
        set_value(key, val)

    if lot is None:
        log.warning(f"position_sizer | publish incomplete | detail={detail}")
    else:
        log.info(
            "position_sizer | published "
            f"{detail['symbol']} balance={bal} risk%={risk_percent} sl_pips={sl_pips} "
            f"pip_val={detail.get('pip_value')} risk_amt={detail.get('risk_amount')} "
            f"lot={lot}"
        )

    return payload | {"detail": detail}


def run_position_sizer_test():
    """
    Quick regression trio - mirrors `scripts/test_position_sizer.py` examples.

    Publishes each scenario sequentially (Redis ends on the last row).
    """
    scenarios = [
        ("EURUSD", 100_000, 0.5, 20),
        ("XAUUSD", 100_000, 1.0, 50),
        ("BTCUSD", 50_000, 0.25, 100),
    ]
    log.info("position_sizer | run_position_sizer_test | starting built-in trio")
    results = []
    for sym, bal, rp, sl in scenarios:
        results.append(publish_position_size(sym, rp, sl, balance=bal))
    log.info("position_sizer | run_position_sizer_test | finished")
    return results


if __name__ == "__main__":
    # Quick sanity trio without the richer prints from `scripts/test_position_sizer.py`.
    run_position_sizer_test()
