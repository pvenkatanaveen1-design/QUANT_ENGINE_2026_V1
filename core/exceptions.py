"""
core/exceptions.py — All custom exceptions for the Quant Forex engine.

WHY THIS FILE EXISTS
--------------------
Catching generic `Exception` hides bugs.  Custom exceptions let you write:

    try:
        router.send_order(signal)
    except KillSwitchActive:
        logger.critical("Kill switch blocked order")
    except SpreadTooWide as e:
        logger.warning(f"Spread blocked: {e}")
    except RiskLimitExceeded as e:
        logger.error(f"Risk limit: {e}")

Each block handles exactly one failure type.  Nothing falls through silently.

2026 FOREX REALITY NOTES
-------------------------
- OrderRejectedError is common on funded accounts when MT5 re-quotes.
  Your executor must handle this gracefully: log, do NOT retry more than once.
- NewsBlackoutActive is raised by news_guard.py proactively.
  The risk engine checks this before even calculating lot size.
- FundedRuleViolation must be permanent — if raised, activate kill switch.
  One funded rule violation = challenge failed = money lost.
- SpreadTooWide is the most common block during Asian session.
  Expected behavior — not an error in system logic.
"""


# ─── BASE ─────────────────────────────────────────────────────────────────────

class QuantaBaseError(Exception):
    """
    Base class for all quant engine errors.
    Catch this to handle ANY engine error without knowing the specific type.
    Usage: except QuantaBaseError as e: logger.error(str(e))
    """


# ─── DATA ERRORS ──────────────────────────────────────────────────────────────

class DataLoadError(QuantaBaseError):
    """
    Failed to load market data from file or broker.
    Raised by: data/hub.py, data/loader.py, core/pulse.py
    Action: Log and retry.  If persistent, halt data-dependent systems.
    """


class BadTickError(QuantaBaseError):
    """
    Tick data is malformed.
    Examples: negative spread, bid > ask, zero or negative price, price spike.
    Raised by: core/sanitizer.py (S32 Tick Sanitizer)
    Action: Discard the tick.  Log for monitoring.  Do not raise an alert unless
            frequency > 10 bad ticks/minute (could indicate feed failure).

    2026 Reality: XAUUSD bad ticks spike during rollover (17:00-17:05 UTC).
    The sanitizer uses ±3×ATR spike filter to catch these.
    """


class StaleDataError(QuantaBaseError):
    """
    Data feed has not updated in the expected time window.
    Raised by: core/heartbeat.py when market:XAUUSD:timestamp is too old.
    Action: Log warning.  If stale > 60 seconds during session, halt trading.

    2026 Reality: MT5 feed can freeze during broker rollover or connectivity issues.
    The heartbeat monitors this every 5 seconds.
    """


# ─── BROKER ERRORS ────────────────────────────────────────────────────────────

class BrokerConnectionError(QuantaBaseError):
    """
    MT5 connection failed or was lost.
    Raised by: execution/broker_bridge.py on mt5.initialize() failure.
    Action: Alert via Telegram.  Retry 3× with exponential backoff.
            If still failing, activate kill switch (cannot monitor open trades).

    2026 Reality: VPS-based MT5 can disconnect during ISP maintenance windows.
    Most recoveries happen within 30-60 seconds.  Only panic after 3 minutes.
    """


class OrderRejectedError(QuantaBaseError):
    """
    Broker rejected the order.
    Common causes: requote, off quotes, market closed, insufficient margin.
    Raised by: execution/broker_bridge.py on mt5.order_send() failure.
    Action: Log the rejection code.  Do NOT retry blindly.
            Requote → retry once with market order.
            Off quotes → skip this signal.
            Insufficient margin → CRITICAL: check DD and account state.
    """


class OrderTimeoutError(QuantaBaseError):
    """
    Order was sent but no fill confirmation arrived within timeout window.
    Raised by: execution/broker_bridge.py after FILL_TIMEOUT_SECONDS.
    Action: Check broker positions directly.  If position exists, log as FILLED.
            If not found, treat as CANCELLED and do not re-send.
    """


# ─── RISK ERRORS ──────────────────────────────────────────────────────────────

class RiskLimitExceeded(QuantaBaseError):
    """
    A hard risk limit was breached.
    Types: daily DD, total DD, max trades per day, max correlated exposure.
    Raised by: risk/shield.py, core/state_store.py
    Action: Block all new orders for the rest of the day (daily DD)
            or permanently (total DD — activate kill switch).

    2026 Reality: Most funded account failures happen because traders ignore
    the first DD warning and keep trading.  This exception exists to enforce
    the boundary mechanically — no override, no 'just one more trade'.
    """


class KillSwitchActive(QuantaBaseError):
    """
    The kill switch is ON.  No new orders permitted.
    Raised by: router.py on every order attempt when kill switch is active.
    Action: Reject the order.  Log.  Alert operator.
    Reset: Manual only — via dashboard UI or Telegram /reset command.

    2026 Reality: Kill switch is the last line of defense.
    Once active, it survives process restarts (stored in SQLite).
    Never auto-deactivate — require human confirmation.
    """


class FundedRuleViolation(QuantaBaseError):
    """
    An action would violate the prop firm's rules.
    Examples: trading during news blackout, exceeding DD limit, holding over weekend.
    Raised by: risk/compliance.py
    Action: Block the action.  Log with specific rule that would be violated.
            If this fires during a live challenge, review your system immediately.

    2026 Reality: FTMO specifically bans:
    - Opening positions at market close Friday (risk of gap)
    - Exceeding daily DD at any point (not just at day end)
    - Having conflicting strategies (hedging is allowed but regulated)
    """


class NewsBlackoutActive(QuantaBaseError):
    """
    Trading is blocked due to upcoming or recent HIGH impact news.
    Raised by: risk/news_guard.py
    Action: Block the order.  Log the news event name and expected time.
    Auto-clears: 30 minutes after the news event time.

    2026 Reality: NFP (first Friday each month) moves gold 30-80 pips in seconds.
    Even with a 20-pip SL, you can get filled 5 pips past it during the spike.
    The $30 saved by skipping NFP trades pays for itself every month.
    """


class SpreadTooWide(QuantaBaseError):
    """
    Current spread exceeds DEFAULT_MAX_SPREAD (default 1.5 pips for XAUUSD).
    Raised by: risk/cost_guard.py
    Action: Block the order.  This is expected behavior — not a bug.
    Retry: cost_guard checks again on next signal.  No manual retry needed.

    2026 Reality: This fires most often during:
    - Asian session (normal behavior — skip)
    - 17:00-17:05 UTC rollover (normal — spread spikes for 5 min)
    - Around news events (news_guard should already block this)
    - Network latency spike (rare but possible)
    """


class CorrelationLimitExceeded(QuantaBaseError):
    """
    Taking this trade would exceed the maximum correlated USD exposure.
    Raised by: risk/correlation.py
    Action: Block the trade.  Wait for existing correlated trade to close.

    2026 Reality: Gold (XAUUSD) is negatively correlated with USD.
    If you already hold EURUSD long (also anti-USD), opening XAUUSD long
    doubles your USD directional risk.  The correlation guard prevents this.
    """


# ─── SIGNAL ERRORS ────────────────────────────────────────────────────────────

class ScoreTooLow(QuantaBaseError):
    """
    Signal confluence score is below minimum threshold (DEFAULT_MIN_SCORE = 60).
    Raised by: risk engine after scoring engine evaluates the signal.
    Action: Discard the signal.  Log the score and which confluence factors failed.
    """


class RegimeNotAllowed(QuantaBaseError):
    """
    Current market regime does not permit this strategy to trade.
    Example: alpha_breakout requires STRONG_TREND but regime is RANGE.
    Raised by: risk engine regime check.
    Action: Block signal.  Log current regime and strategy.
    """


class SessionNotAllowed(QuantaBaseError):
    """
    Current time is outside the allowed trading session.
    Raised by: risk engine session check.
    Action: Block signal.  This fires overnight and during Asian session.
    This is expected and frequent — not an error.
    """


# ─── CONFIG ERRORS ────────────────────────────────────────────────────────────

class ConfigLoadError(QuantaBaseError):
    """
    YAML or .env config file is missing or malformed.
    Raised by: core/config_manager.py on startup.
    Action: CRITICAL — system cannot start without config.
            Print clear error showing which file is missing and path expected.
    """


class MissingConfigKey(QuantaBaseError):
    """
    A required key was not found in YAML or .env.
    Raised by: core/config_manager.py get() and env() functions.
    Action: CRITICAL if required=True.  Warning if optional with default.
    """
