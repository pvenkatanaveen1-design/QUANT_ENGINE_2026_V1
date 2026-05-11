"""Daily drawdown tracker — funded-style UTC day bucket + peak/low equity (Redis only, no MT5)."""

from __future__ import annotations

import time
from datetime import date, datetime, timezone

from core.bus import get_value, set_value
from core.config import load_config
from core.logger import get_logger

log = get_logger()

# How often the tracker writes dd:* keys (seconds).
TRACKER_INTERVAL_SECONDS = 5

# In-process UTC date so we detect midnight rollover even if Redis is empty on first run.
_last_tracked_utc_date: date | None = None


def _parse_numeric(raw: object) -> float | None:
    """Parse Redis JSON number/string into float; None if unusable."""
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


def _utc_today() -> date:
    """Calendar date in UTC (funded daily buckets almost always use UTC)."""
    return datetime.now(timezone.utc).date()


def _read_account_snapshot() -> tuple[float | None, float | None]:
    """Pull broker snapshot keys another module is expected to publish."""
    bal = _parse_numeric(get_value("account:balance"))
    eq = _parse_numeric(get_value("account:equity"))
    return bal, eq


def _day_open_balance(balance: float | None, equity: float | None) -> float | None:
    """
    Start-of-day reference: prefer `account:balance`, else first `account:equity`.

    Beginners: many prop rules anchor daily DD to the balance at 00:00 UTC; if balance
    is missing in simulation, equity is used instead so math still runs.
    """
    if balance is not None and balance > 0:
        return balance
    if equity is not None and equity > 0:
        return equity
    return None


def update_peak_equity(current_peak: float | None, equity: float | None) -> float | None:
    """Higher intraday equity watermark."""
    if equity is None:
        return current_peak
    if current_peak is None:
        return float(equity)
    return max(float(current_peak), float(equity))


def update_lowest_equity(current_low: float | None, equity: float | None) -> float | None:
    """Lower intraday equity watermark."""
    if equity is None:
        return current_low
    if current_low is None:
        return float(equity)
    return min(float(current_low), float(equity))


def calculate_current_daily_dd(start_balance: float | None, equity: float | None) -> float | None:
    """
    Daily DD% vs start-of-day reference balance.

    Formula: max(0, (start - equity) / start * 100). Profit above start → 0% DD here.
    """
    if start_balance is None or equity is None:
        return None
    if start_balance <= 0:
        return None
    return max(0.0, (float(start_balance) - float(equity)) / float(start_balance) * 100.0)


def calculate_max_daily_dd(prev_max: float | None, current_dd: float | None) -> float | None:
    """Worst (highest) daily DD% seen so far today."""
    if current_dd is None:
        return prev_max
    if prev_max is None:
        return float(current_dd)
    return max(float(prev_max), float(current_dd))


def _tracker_status_token(
    current_dd: float | None,
    warn_pct: float,
    block_pct: float,
    data_ok: bool,
) -> str:
    """Reserved for tests / future use — dashboard reads `dd:tracker_status` from Redis."""
    if not data_ok:
        return "ERROR"
    if current_dd is None:
        return "STALE_DATA"
    if current_dd >= block_pct:
        return "DANGER"
    if current_dd >= warn_pct:
        return "WARNING"
    return "HEALTHY"


def reset_daily_tracking() -> dict:
    """
    Begin a new UTC calendar day bucket.

    Reads `account:balance` / `account:equity`, seeds start, peak, low, and zeros max DD.
    Returns a dict of fields ready for `publish_drawdown_status`.
    """
    balance, equity = _read_account_snapshot()
    start = _day_open_balance(balance, equity)

    now_ts = datetime.now(timezone.utc).timestamp()

    if equity is None or start is None:
        log.warning("drawdown_tracker | reset skipped — missing balance/equity for day open")
        return {
            "dd:start_balance": start,
            "dd:peak_equity": equity,
            "dd:lowest_equity": equity,
            "dd:current_daily_dd": None,
            "dd:max_daily_dd": 0.0,
            "dd:last_reset": now_ts,
            "dd:tracker_status": "ERROR",
        }

    peak = float(equity)
    low = float(equity)
    cur_dd = calculate_current_daily_dd(start, equity)
    max_dd = calculate_max_daily_dd(0.0, cur_dd)

    log.info(
        f"drawdown_tracker | UTC day reset | start_balance={start} equity={equity} peak={peak}"
    )

    return {
        "dd:start_balance": float(start),
        "dd:peak_equity": peak,
        "dd:lowest_equity": low,
        "dd:current_daily_dd": cur_dd,
        "dd:max_daily_dd": max_dd,
        "dd:last_reset": now_ts,
        "dd:tracker_status": "HEALTHY",
    }


def initialize_daily_tracking() -> dict:
    """
    Cold-start when Redis has no usable tracker state.

    Same seeding rules as reset; use when `dd:last_reset` is missing.
    """
    log.info("drawdown_tracker | initialize_daily_tracking (cold start)")
    return reset_daily_tracking()


def publish_drawdown_status(fields: dict) -> None:
    """Write all dd:* keys from one snapshot dict."""
    cfg = load_config()
    warn_pct = float(cfg.get("DAILY_DD_WARNING", 3.0))
    block_pct = float(cfg.get("DAILY_DD_BLOCK", 5.0))
    bal, eq = _read_account_snapshot()
    data_ok = bal is not None or eq is not None
    cur_dd = fields.get("dd:current_daily_dd")
    if cur_dd is not None:
        try:
            cur_dd = float(cur_dd)
        except (TypeError, ValueError):
            cur_dd = None
    status = _tracker_status_token(cur_dd, warn_pct, block_pct, data_ok)

    out = dict(fields)
    out["dd:tracker_status"] = status

    for key, value in out.items():
        if not str(key).startswith("dd:"):
            continue
        try:
            set_value(str(key), value)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"drawdown_tracker | publish failed key={key} err={exc}")

    log.info(
        f"drawdown_tracker | publish | cur_dd={out.get('dd:current_daily_dd')} "
        f"max_dd={out.get('dd:max_daily_dd')} status={status}"
    )


def _load_previous_state() -> dict:
    """Read existing dd:* from Redis to continue the same UTC day after restarts."""
    return {
        "dd:start_balance": _parse_numeric(get_value("dd:start_balance")),
        "dd:peak_equity": _parse_numeric(get_value("dd:peak_equity")),
        "dd:lowest_equity": _parse_numeric(get_value("dd:lowest_equity")),
        "dd:max_daily_dd": _parse_numeric(get_value("dd:max_daily_dd")),
        "dd:last_reset": _parse_numeric(get_value("dd:last_reset")),
    }


def _last_reset_utc_date(last_reset_ts: float | None) -> date | None:
    if last_reset_ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(last_reset_ts), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def evaluate_drawdown_once() -> dict:
    """
    Single evaluation cycle: rollover detection, peak/low, DD%, publish.

    Safe to call from tests after pushing `account:*` snapshots.
    """
    global _last_tracked_utc_date

    today = _utc_today()
    prev = _load_previous_state()
    reset_date = _last_reset_utc_date(prev["dd:last_reset"])

    need_reset = False
    if reset_date is None or reset_date != today:
        need_reset = True
    if _last_tracked_utc_date is not None and _last_tracked_utc_date != today:
        need_reset = True

    if need_reset:
        fields = reset_daily_tracking()
        _last_tracked_utc_date = today
        publish_drawdown_status(fields)
        return fields

    if _last_tracked_utc_date is None:
        _last_tracked_utc_date = reset_date or today

    balance, equity = _read_account_snapshot()
    start = prev["dd:start_balance"]
    if start is None:
        start = _day_open_balance(balance, equity)
    if start is None:
        fields = initialize_daily_tracking()
        _last_tracked_utc_date = today
        publish_drawdown_status(fields)
        return fields

    if equity is None:
        fields = {
            "dd:start_balance": start,
            "dd:peak_equity": prev["dd:peak_equity"],
            "dd:lowest_equity": prev["dd:lowest_equity"],
            "dd:current_daily_dd": None,
            "dd:max_daily_dd": prev["dd:max_daily_dd"],
            "dd:last_reset": prev["dd:last_reset"],
            "dd:tracker_status": "ERROR",
        }
        publish_drawdown_status(fields)
        return fields

    peak = update_peak_equity(prev["dd:peak_equity"], equity)
    low = update_lowest_equity(prev["dd:lowest_equity"], equity)
    cur_dd = calculate_current_daily_dd(start, equity)
    max_dd = calculate_max_daily_dd(prev["dd:max_daily_dd"], cur_dd)

    fields = {
        "dd:start_balance": float(start),
        "dd:peak_equity": peak,
        "dd:lowest_equity": low,
        "dd:current_daily_dd": cur_dd,
        "dd:max_daily_dd": max_dd,
        "dd:last_reset": prev["dd:last_reset"],
        "dd:tracker_status": "HEALTHY",
    }
    publish_drawdown_status(fields)
    return fields


def run_drawdown_tracker() -> None:
    """Daemon loop: publish dd:* about every TRACKER_INTERVAL_SECONDS."""
    log.info(f"drawdown_tracker | daemon start | interval_s={TRACKER_INTERVAL_SECONDS}")
    while True:
        try:
            evaluate_drawdown_once()
        except Exception:  # noqa: BLE001
            log.exception("drawdown_tracker | tick failed")
        time.sleep(TRACKER_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_drawdown_tracker()
