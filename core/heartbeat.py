"""System heartbeat engine for health and stale-data monitoring."""

# We import time to run periodic checks every few seconds.
import time
# We import datetime so we can compare age of timestamps safely.
from datetime import datetime, timezone

# We import Redis bus helpers to read and publish health values.
from core.bus import get_value, set_value
# We import project logger for clean and consistent logs.
from core.logger import get_logger
# Shared normalization keeps logs and UI aligned (see normalize_status docstring).
from core.status_normalize import normalize_status, rollup_overall


# We create one shared logger for this module.
log = get_logger()

# We keep monitoring constants in one place for easy tuning later.
MARKET_STALE_SECONDS = 15
CLOCK_STALE_SECONDS = 20
HEARTBEAT_INTERVAL_SECONDS = 5


def _utc_now_ts():
    """Return current UTC timestamp in seconds."""
    return datetime.utcnow().timestamp()


def _extract_timestamp_seconds(raw_value):
    """
    Convert different timestamp formats into unix seconds.

    Supported inputs:
    - int/float unix seconds (or milliseconds)
    - string unix seconds
    - ISO datetime strings like '2026-05-07T11:30:00Z'
    - dicts containing keys like timestamp/ts/time/updated_at
    """
    if raw_value is None:
        return None

    # If value comes as dictionary, try common timestamp field names.
    if isinstance(raw_value, dict):
        for key in ("timestamp", "ts", "time", "updated_at"):
            if key in raw_value:
                return _extract_timestamp_seconds(raw_value.get(key))
        return None

    # If numeric, treat as unix time. Detect milliseconds automatically.
    if isinstance(raw_value, (int, float)):
        value = float(raw_value)
        if value > 1_000_000_000_000:
            return value / 1000.0
        return value

    # If string, try float first, then ISO datetime parsing.
    if isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return None
        try:
            value = float(text)
            if value > 1_000_000_000_000:
                return value / 1000.0
            return value
        except ValueError:
            pass

        try:
            if text.endswith("Z"):
                text = text.replace("Z", "+00:00")
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return None

    return None


def _parse_clock_utc_display(raw_value):
    """
    Parse clock:utc style strings ('YYYY-MM-DD HH:%M:%S') as UTC for age checks.

    core.clock publishes UTC wall time without an explicit offset; we treat it as UTC.
    """
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        return None
    text = raw_value.strip()
    if not text:
        return None
    try:
        dt_naive = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        return dt_naive.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def evaluate_pulse():
    """
    Classify pulse pipeline state.

    Returns:
      (severity, pulse_running_ok)
      severity in ok | warn | critical
    """
    raw = get_value("pulse:status")
    token = normalize_status(raw, aspect="lifecycle")
    log.info(f"heartbeat | pulse raw={raw!r} normalized(lifecycle)={token}")

    if raw is None:
        log.warning("heartbeat | pulse key missing: pulse:status")
        return "critical", False

    text = str(raw).strip().lower()
    if text == "running":
        return "ok", True
    if text == "starting":
        return "warn", True
    return "critical", False


def evaluate_mt5():
    """MT5 connection using connection tokens (connected/disconnected)."""
    raw = get_value("mt5:connection")
    conn_token = normalize_status(raw, aspect="connection")
    connected = conn_token == "connected"
    log.info(f"heartbeat | mt5 raw={raw!r} normalized(connection)={conn_token}")

    if raw is None:
        log.warning("heartbeat | mt5 key missing: mt5:connection")
        return "critical", False

    return ("ok" if connected else "critical"), connected


def evaluate_market():
    """Market freshness from XAUUSD timestamp."""
    raw = get_value("market:XAUUSD:timestamp")
    market_ts = _extract_timestamp_seconds(raw)

    if market_ts is None:
        log.warning("heartbeat | market timestamp missing/invalid: market:XAUUSD:timestamp")
        stale_tok = normalize_status(False, aspect="freshness")
        log.info(f"heartbeat | market raw={raw!r} normalized(freshness)={stale_tok}")
        return "critical", False

    age_seconds = _utc_now_ts() - market_ts
    is_fresh = age_seconds <= MARKET_STALE_SECONDS
    fresh_tok = normalize_status(is_fresh, aspect="freshness")
    log.info(
        f"heartbeat | market raw={raw!r} age_seconds={age_seconds:.2f} "
        f"normalized(freshness)={fresh_tok}"
    )
    return ("ok" if is_fresh else "critical"), is_fresh


def evaluate_clock():
    """
    Clock pipeline: lifecycle on clock:status plus optional age from embedded ts or clock:utc.

    core.clock publishes clock:status as lifecycle strings like 'running', not unix timestamps.
    """
    raw = get_value("clock:status")
    lifecycle_token = normalize_status(raw, aspect="lifecycle")
    lifecycle_ok = lifecycle_token == "running"

    clock_ts = _extract_timestamp_seconds(raw)
    if clock_ts is None:
        utc_hint = get_value("clock:utc")
        clock_ts = _parse_clock_utc_display(utc_hint)

    explicit_token = None
    if isinstance(raw, dict) and "healthy" in raw:
        explicit_token = normalize_status(raw.get("healthy"), aspect="health")

    if explicit_token == "unhealthy":
        stale_tok = normalize_status(False, aspect="freshness")
        log.warning(f"heartbeat | clock explicit unhealthy | raw={raw!r} normalized(freshness)={stale_tok}")
        return "critical", False

    if raw is None:
        log.warning("heartbeat | clock:status missing")

    if clock_ts is None:
        age_ok = lifecycle_ok
    else:
        age_seconds = _utc_now_ts() - clock_ts
        age_ok = age_seconds <= CLOCK_STALE_SECONDS
        log.info(f"heartbeat | clock_age_seconds={age_seconds:.2f} | age_ok={age_ok}")

    result = bool(lifecycle_ok and age_ok)
    fresh_tok = normalize_status(result, aspect="freshness")
    log.info(
        "heartbeat | clock "
        f"raw={raw!r} lifecycle_norm={lifecycle_token} "
        f"explicit_norm={explicit_token} normalized(freshness)={fresh_tok} clock_fresh={result}"
    )

    if raw is None:
        return "critical", False
    if not lifecycle_ok:
        return "critical", False
    if not age_ok:
        return "critical", False
    return "ok", True


def calculate_overall_health_status(pulse_sev, mt5_sev, market_sev, clock_sev):
    """
    Roll subsystem severities into HEALTHY / DEGRADED / UNHEALTHY.

    critical -> UNHEALTHY; else any warn -> DEGRADED; else HEALTHY.
    """
    overall = rollup_overall([pulse_sev, mt5_sev, market_sev, clock_sev])
    log.info(
        "heartbeat | overall rollup "
        f"pulse={pulse_sev} mt5={mt5_sev} market={market_sev} clock={clock_sev} -> {overall}"
    )
    return overall


def publish_heartbeat():
    """Run one heartbeat cycle and publish all required Redis health keys."""
    now_ts = _utc_now_ts()

    pulse_sev, pulse_ok = evaluate_pulse()
    mt5_sev, mt5_ok = evaluate_mt5()
    market_sev, market_ok = evaluate_market()
    clock_sev, clock_ok = evaluate_clock()

    overall = calculate_overall_health_status(pulse_sev, mt5_sev, market_sev, clock_sev)

    # Scalar mirrors for dashboards that still read booleans from heartbeat:* keys.
    set_value("heartbeat:status", "running")
    set_value("heartbeat:last_check", now_ts)
    set_value("heartbeat:market_fresh", market_ok)
    set_value("heartbeat:clock_fresh", clock_ok)
    set_value("heartbeat:mt5_healthy", mt5_ok)
    set_value("heartbeat:overall", overall)

    log.info(
        "heartbeat | publish summary "
        f"overall={overall} pulse_sev={pulse_sev} mt5_sev={mt5_sev} "
        f"market_sev={market_sev} clock_sev={clock_sev} "
        f"bools pulse_ok={pulse_ok} mt5_ok={mt5_ok} market_ok={market_ok} clock_ok={clock_ok}"
    )

    return {
        "last_check": now_ts,
        "pulse_ok": pulse_ok,
        "mt5_healthy": mt5_ok,
        "market_fresh": market_ok,
        "clock_fresh": clock_ok,
        "overall": overall,
        "pulse_severity": pulse_sev,
        "mt5_severity": mt5_sev,
        "market_severity": market_sev,
        "clock_severity": clock_sev,
    }


def run_heartbeat():
    """Run heartbeat forever, publishing health every HEARTBEAT_INTERVAL_SECONDS."""
    log.info(
        "heartbeat | started | "
        f"interval={HEARTBEAT_INTERVAL_SECONDS}s market_stale={MARKET_STALE_SECONDS}s "
        f"clock_stale={CLOCK_STALE_SECONDS}s"
    )

    while True:
        try:
            publish_heartbeat()
        except Exception as error:  # noqa: BLE001 - keep loop alive for resilience.
            log.error(f"heartbeat | cycle failed | error={error}")
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        run_heartbeat()
    except KeyboardInterrupt:
        log.info("heartbeat | stopped by user")
