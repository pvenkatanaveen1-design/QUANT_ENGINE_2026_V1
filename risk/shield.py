"""Funded-account drawdown shield - simulated equity monitoring and BLOCK escalation."""

# We import time so we can sleep between Redis publishes on a fixed interval.
import time
# We import datetime so we can roll daily peaks at UTC midnight cleanly.
from datetime import date, datetime, timezone

# Redis helpers keep Redis details centralized for beginner-friendly reuse.
from core.bus import get_value, set_value
from core.config import load_config
from core.system_mode import get_system_mode
from core.logger import get_logger


log = get_logger()

# Publish cadence for risk:* keys (seconds).
SHIELD_INTERVAL_SECONDS = 5

ShieldState = str  # "SAFE" | "WARNING" | "BLOCKED"

# Running peaks kept in memory across ticks (restart resets peaks - see shield.txt).
_session_peak_equity: float | None = None
_daily_peak_equity: float | None = None
_last_utc_date: date | None = None

# Log loaded limits once per process so startup logs stay readable.
_config_logged = False

# Log SYSTEM MODE banner once per process (shield daemon + inline publishes share this flag).
_system_mode_logged = False


def _log_system_mode_banner() -> None:
    """Emit a single SYSTEM MODE block so beginners always see TEST vs LIVE in logs."""
    global _system_mode_logged
    if _system_mode_logged:
        return
    _system_mode_logged = True
    mode = get_system_mode()
    log.info("--------------------------------")
    log.info(f"SYSTEM MODE: {mode}")
    if mode == "TEST":
        log.info("Using simulated account values")
    else:
        log.info("Real broker values expected")
    log.info("--------------------------------")


def _log_active_configuration(cfg: dict) -> None:
    """Emit human-readable risk limits (matches `.env` / `core.config.load_config`)."""
    global _config_logged
    if _config_logged:
        return
    _config_logged = True
    firm = cfg["PROP_FIRM_NAME"]
    log.info(f"{firm} risk rules loaded")
    log.info(f"Daily warning = {cfg['DAILY_DD_WARNING']}%")
    log.info(f"Daily block = {cfg['DAILY_DD_BLOCK']}%")
    log.info(f"Max DD block = {cfg['MAX_DD_BLOCK']}%")
    log.info(
        f"Max DD approach (WARNING band) = >= {cfg['MAX_DD_APPROACH']}% below session peak "
        f"(before {cfg['MAX_DD_BLOCK']}% block)"
    )
    log.info(
        f"Account size (reference) = {cfg['ACCOUNT_SIZE']} | "
        f"Default risk per trade = {cfg['DEFAULT_RISK_PER_TRADE']}%"
    )


def _parse_numeric(raw):
    """
    Parse Redis JSON number/string into float.

    Returns None when missing or unusable so callers can degrade gracefully.
    """
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


def _today_utc() -> date:
    """Calendar date in UTC (used only for day rollover detection)."""
    return datetime.now(timezone.utc).date()


def calculate_drawdown(current_equity: float | None, peak_equity: float | None) -> float | None:
    """
    Max-style drawdown from the session peak: drop from peak to current equity.

    Formula: (peak - current) / peak * 100. Returns None if inputs invalid or peak <= 0.
    """
    if current_equity is None or peak_equity is None:
        return None
    if peak_equity <= 0:
        return None
    return max(0.0, (peak_equity - current_equity) / peak_equity * 100.0)


def calculate_daily_drawdown(current_equity: float | None, daily_peak_equity: float | None) -> float | None:
    """
    Daily intraday drawdown from today's high-water equity mark.

    Same formula as max DD but resets each UTC calendar day when peaks roll forward.
    """
    return calculate_drawdown(current_equity, daily_peak_equity)


def _update_peak_trackers(equity: float) -> None:
    """
    Advance session + daily peaks from one equity observation.

    New UTC day: daily peak resets to this tick (common funded-account daily DD definition).
    """
    global _session_peak_equity, _daily_peak_equity, _last_utc_date

    today = _today_utc()
    if _last_utc_date != today:
        _daily_peak_equity = equity
        _last_utc_date = today
        log.info(f"shield | new UTC day | daily peak reset -> {equity}")

    _session_peak_equity = equity if _session_peak_equity is None else max(_session_peak_equity, equity)
    _daily_peak_equity = equity if _daily_peak_equity is None else max(_daily_peak_equity, equity)


def evaluate_risk_state(daily_dd_pct: float | None, max_dd_pct: float | None, cfg: dict) -> ShieldState:
    """
    Map drawdown percentages to shield machine state using limits from `cfg` (see `.env`).

    BLOCKED:
      - daily DD at or above DAILY_DD_BLOCK, or
      - max DD at or above MAX_DD_BLOCK.

    WARNING:
      - daily DD at or above DAILY_DD_WARNING but not blocked yet, or
      - max DD in [MAX_DD_APPROACH, MAX_DD_BLOCK).

    SAFE:
      - otherwise. Missing DD legs are treated as 0.0 unless both are None.
    """
    if daily_dd_pct is None and max_dd_pct is None:
        return "SAFE"

    daily_warn = cfg["DAILY_DD_WARNING"]
    daily_block = cfg["DAILY_DD_BLOCK"]
    max_block = cfg["MAX_DD_BLOCK"]
    max_approach = cfg["MAX_DD_APPROACH"]

    d = 0.0 if daily_dd_pct is None else daily_dd_pct
    m = 0.0 if max_dd_pct is None else max_dd_pct

    if d >= daily_block or m >= max_block:
        return "BLOCKED"
    if d >= daily_warn or (m >= max_approach and m < max_block):
        return "WARNING"
    return "SAFE"


def publish_risk_status() -> dict:
    """
    One evaluate/publish cycle: read account snapshot, compute DD, write risk:* keys.

    Missing equity halts peak updates and emits conservative SAFE + null DD fields with warnings.
    """
    _log_system_mode_banner()
    cfg = load_config()
    _log_active_configuration(cfg)

    now_ts = datetime.now(timezone.utc).timestamp()
    balance_raw = get_value("account:balance")
    equity_raw = get_value("account:equity")

    balance = _parse_numeric(balance_raw)
    equity = _parse_numeric(equity_raw)

    log.info(f"shield | raw Redis account:balance={balance_raw!r} account:equity={equity_raw!r}")

    if equity is None:
        log.warning("shield | missing or invalid account:equity - cannot compute drawdown")
        payload = {
            "risk:daily_dd": None,
            "risk:max_dd": None,
            "risk:shield": "SAFE",
            "risk:status": "SAFE",
            "risk:block_trading": False,
            "risk:last_update": now_ts,
        }
        for key, val in payload.items():
            set_value(key, val)
        return payload

    _update_peak_trackers(equity)

    daily_dd = calculate_daily_drawdown(equity, _daily_peak_equity)
    max_dd = calculate_drawdown(equity, _session_peak_equity)

    state = evaluate_risk_state(daily_dd, max_dd, cfg)
    block = state == "BLOCKED"

    log.info(
        f"shield | equity={equity} balance={balance} daily_peak={_daily_peak_equity} "
        f"session_peak={_session_peak_equity} daily_dd={daily_dd} max_dd={max_dd} "
        f"state={state} block_trading={block}"
    )

    payload = {
        "risk:daily_dd": daily_dd,
        "risk:max_dd": max_dd,
        "risk:shield": state,
        "risk:status": state,
        "risk:block_trading": block,
        "risk:last_update": now_ts,
    }
    for key, val in payload.items():
        set_value(key, val)

    return payload


def run_shield() -> None:
    """Run the shield loop forever, publishing risk state every SHIELD_INTERVAL_SECONDS."""
    _log_system_mode_banner()
    _log_active_configuration(load_config())
    log.info(f"shield | daemon started | publish every {SHIELD_INTERVAL_SECONDS}s")
    while True:
        try:
            publish_risk_status()
        except Exception as error:  # noqa: BLE001 - keep funded guard alive.
            log.error(f"shield | cycle failed | error={error}")
        time.sleep(SHIELD_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        run_shield()
    except KeyboardInterrupt:
        log.info("shield | stopped by user")
