"""
systems/data/tick_sanitizer.py — S32: Tick Sanitizer.

WHY THIS FILE EXISTS
--------------------
Raw MT5 ticks can contain:
  1. Duplicate timestamps (broker re-sends same tick)
  2. Negative spreads (bid > ask — data feed glitch)
  3. Price jumps (5% move in one tick = stale reconnect artifact)
  4. Spread explosions (50+ pips spread = news widening or broker issue)
  5. Stale ticks (same timestamp as previous — broker frozen)
  6. Future timestamps (clock sync issues on VPS)

Feeding bad ticks to your strategy causes:
  - False signals based on phantom price moves
  - Incorrect ATR/ADX values distorting regime detection
  - Position sizing errors (wrong price = wrong lot size)

This sanitizer sits BETWEEN core/pulse.py and market_data_hub.py:

  MT5 → pulse.py → EventBus(RAW_MARKET_DATA)
  → tick_sanitizer._on_raw_tick()  ← validates here
  → EventBus(MARKET_DATA clean) or EventBus(TICK_REJECTED)
  → market_data_hub._on_market_data()

2026 XAUUSD VALIDATION THRESHOLDS:
  MAX_SPREAD_PIPS = 50.0:  During news, spreads can hit 40-50 pips.
    Above 50 is almost certainly a data feed error or broker manipulation.
  MAX_PRICE_JUMP_PCT = 3.0:  A 3% move in XAUUSD in one tick would be
    ~$70 on gold at $2350.  This is physically impossible intraday.
    If you see this, it's a bad tick from a stale reconnection.
  STALE_TICK_SECONDS = 5.0:  Same timestamp for 5 seconds = broker frozen.

QUALITY SCORE:
  Tracks last 1000 ticks.  Score = (clean / total) × 100.
  If score drops below 90%, publish DATA_QUALITY_ALERT.
  A score of 100% = perfect data feed.
  A score of 95% is acceptable.
  Below 80% = check broker connection / reconsider broker.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta, timezone

from core.event_bus import EventType, bus
from core.logger import get_logger, LogCategory

log = get_logger("tick_sanitizer", LogCategory.DATA)

# ─── VALIDATION THRESHOLDS ───────────────────────────────────────────────────
# Override in config/symbols.yaml → loaded at startup.
# These defaults cover XAUUSD ECN trading in 2026.

MAX_SPREAD_PIPS     = 50.0   # pips — above this = news or broker glitch
MIN_SPREAD_PIPS     = 0.0    # pips — negative spread = data error (bid > ask)
MAX_PRICE_JUMP_PCT  = 3.0    # % — one tick move above this = stale reconnect
MIN_BID             = 100.0  # USD — XAUUSD cannot be below $100 (sanity check)
MAX_BID             = 10000.0 # USD — XAUUSD cannot be above $10,000 (sanity)
STALE_TICK_SECONDS  = 5.0    # seconds — same timestamp for this long = frozen feed
QUALITY_ALERT_THRESHOLD = 90.0  # % — alert if quality score drops below this

# Ring buffer for quality tracking (last 1000 ticks)
_QUALITY_BUFFER_SIZE = 1000


class TickSanitizer:
    """
    Validates and cleans raw tick data before storage and analysis.

    Subscribe to RAW_MARKET_DATA events from pulse.py.
    Publish clean MARKET_DATA events that market_data_hub consumes.
    Publish TICK_REJECTED events for rejected ticks (dashboard tracking).

    Singleton — import via: from systems.data.tick_sanitizer import sanitizer
    """

    def __init__(self) -> None:
        # Per-symbol state tracking
        self._last_tick:  dict[str, dict]  = {}  # symbol → last valid tick
        self._lock        = threading.Lock()

        # Quality tracking buffer: 1 = clean, 0 = rejected
        self._quality_buf: deque[int]  = deque(maxlen=_QUALITY_BUFFER_SIZE)
        self._total_seen:  int         = 0
        self._total_clean: int         = 0
        self._total_rejected: int      = 0

        # Rejection breakdown by reason
        self._rejection_counts: dict[str, int] = {}

        # Config (can be overridden after startup from config_manager)
        self.max_spread_pips    = MAX_SPREAD_PIPS
        self.min_spread_pips    = MIN_SPREAD_PIPS
        self.max_price_jump_pct = MAX_PRICE_JUMP_PCT
        self.min_bid            = MIN_BID
        self.max_bid            = MAX_BID
        self.stale_seconds      = STALE_TICK_SECONDS

        self._running = False
        log.info("TickSanitizer initialized")

    def start(self) -> None:
        """
        Subscribe to raw RAW_MARKET_DATA events.
        pulse.py must publish raw ticks here; MARKET_DATA is reserved for clean ticks.
        """
        bus.subscribe(EventType.RAW_MARKET_DATA, self._on_raw_tick)
        self._running = True
        log.info("TickSanitizer started — intercepting RAW_MARKET_DATA events")

    def stop(self) -> None:
        bus.unsubscribe(EventType.RAW_MARKET_DATA, self._on_raw_tick)
        self._running = False
        log.info("TickSanitizer stopped")

    # ─── EVENT HANDLER ────────────────────────────────────────────────────────

    def _on_raw_tick(self, event) -> None:
        """
        Validate incoming tick.  Runs in EventBus thread pool.
        If valid: re-publish as clean MARKET_DATA.
        If invalid: publish TICK_REJECTED with reason.
        """
        payload = event.payload
        if not isinstance(payload, dict):
            return

        symbol = payload.get("symbol", "")
        if not symbol:
            return

        with self._lock:
            self._total_seen += 1

        is_valid, rejection_reason = self._validate(symbol, payload)

        if is_valid:
            with self._lock:
                self._total_clean += 1
                self._quality_buf.append(1)
                self._last_tick[symbol] = payload

            # Forward clean tick to market_data_hub
            # The market_data_hub also subscribes to MARKET_DATA — so we need
            # to re-publish.  Use a tagged payload to avoid double-processing.
            clean_payload = dict(payload)
            clean_payload["sanitized"] = True

            bus.publish(
                EventType.MARKET_DATA,
                clean_payload,
                source="tick_sanitizer",
                correlation_id=event.correlation_id,
            )

            # Check quality score after every 100 ticks
            if self._total_seen % 100 == 0:
                self._check_quality_alert(symbol)
        else:
            with self._lock:
                self._total_rejected += 1
                self._quality_buf.append(0)
                self._rejection_counts[rejection_reason] = (
                    self._rejection_counts.get(rejection_reason, 0) + 1
                )

            bus.publish(
                EventType.TICK_REJECTED,
                {
                    "symbol":  symbol,
                    "reason":  rejection_reason,
                    "payload": str(payload)[:200],
                },
                source="tick_sanitizer",
            )
            log.debug(f"Tick rejected [{rejection_reason}]: {symbol}")

    # ─── VALIDATION LOGIC ─────────────────────────────────────────────────────

    def _validate(self, symbol: str, tick: dict) -> tuple[bool, str]:
        """
        Run all validation checks on a raw tick.
        Returns (is_valid: bool, rejection_reason: str).
        """
        # 1. Clean ticks should never return to this raw boundary.
        if tick.get("sanitized"):
            return False, "SANITIZED_TICK_ON_RAW_CHANNEL"

        bid = tick.get("bid", 0.0)
        ask = tick.get("ask", 0.0)

        # 2. Price sanity bounds
        if bid <= 0 or ask <= 0:
            return False, "ZERO_PRICE"
        if bid < self.min_bid or bid > self.max_bid:
            return False, f"PRICE_OUT_OF_RANGE:{bid:.2f}"
        if ask < self.min_bid or ask > self.max_bid:
            return False, f"PRICE_OUT_OF_RANGE:{ask:.2f}"

        # 3. Negative spread (bid > ask = data error)
        if bid > ask:
            return False, "NEGATIVE_SPREAD"

        # 4. Spread explosion
        spread_pips = (ask - bid) / 0.10
        if spread_pips > self.max_spread_pips:
            return False, f"SPREAD_TOO_WIDE:{spread_pips:.1f}pips"

        # 5. Timestamp check
        time_raw = tick.get("time")
        if time_raw is None:
            return False, "NO_TIMESTAMP"

        tick_time = time_raw if isinstance(time_raw, datetime) else None
        if isinstance(time_raw, str):
            try:
                tick_time = datetime.fromisoformat(time_raw)
            except ValueError:
                return False, "INVALID_TIMESTAMP"

        if tick_time is None:
            return False, "INVALID_TIMESTAMP"

        # Future timestamp guard (allow 30s clock skew).
        # Use timedelta to avoid invalid datetime values when seconds overflow.
        now_utc = datetime.utcnow()
        future_cutoff = now_utc + timedelta(seconds=30)
        if tick_time.replace(tzinfo=None) > future_cutoff:
            return False, "FUTURE_TIMESTAMP"

        # 6. Stale tick (same timestamp as previous)
        with self._lock:
            last = self._last_tick.get(symbol)
        if last:
            last_time = last.get("time")
            if isinstance(last_time, str):
                try:
                    last_time = datetime.fromisoformat(last_time)
                except ValueError:
                    last_time = None
            if last_time and isinstance(tick_time, datetime):
                seconds_diff = abs((tick_time - last_time).total_seconds())
                if seconds_diff < 0.001:  # same millisecond = duplicate
                    return False, "DUPLICATE_TIMESTAMP"

        # 7. Price jump check vs previous tick
        if last and last.get("bid", 0):
            last_bid = last["bid"]
            pct_change = abs(bid - last_bid) / last_bid * 100
            if pct_change > self.max_price_jump_pct:
                return False, f"PRICE_JUMP:{pct_change:.1f}%"

        return True, ""

    def _check_quality_alert(self, symbol: str) -> None:
        """Publish DATA_QUALITY_ALERT if quality score is below threshold."""
        score = self.quality_score
        if score < QUALITY_ALERT_THRESHOLD:
            bus.publish(
                EventType.DATA_QUALITY_ALERT,
                {
                    "symbol":        symbol,
                    "quality_score": score,
                    "total_seen":    self._total_seen,
                    "total_clean":   self._total_clean,
                    "total_rejected": self._total_rejected,
                    "rejection_breakdown": dict(self._rejection_counts),
                },
                source="tick_sanitizer",
            )
            log.warning(
                f"Data quality alert: {symbol} quality={score:.1f}% "
                f"(threshold={QUALITY_ALERT_THRESHOLD}%)"
            )

    # ─── DIAGNOSTICS ──────────────────────────────────────────────────────────

    @property
    def quality_score(self) -> float:
        """
        Current feed quality score (0-100%).
        Based on the last 1000 ticks (or all ticks if fewer seen).
        100% = perfect feed.  Below 90% = investigate broker connection.
        """
        buf = self._quality_buf
        if not buf:
            return 100.0
        return round(sum(buf) / len(buf) * 100, 1)

    def get_stats(self) -> dict:
        """
        Return sanitizer metrics for dashboard display.

        Keys:
            quality_score         — current feed quality percentage
            total_seen            — total ticks received from MT5
            total_clean           — ticks that passed validation
            total_rejected        — ticks that failed validation
            rejection_breakdown   — dict of reason → count
            running               — bool
        """
        with self._lock:
            return {
                "quality_score":      self.quality_score,
                "total_seen":         self._total_seen,
                "total_clean":        self._total_clean,
                "total_rejected":     self._total_rejected,
                "rejection_breakdown": dict(self._rejection_counts),
                "running":            self._running,
            }


# ─── SINGLETON ────────────────────────────────────────────────────────────────
sanitizer = TickSanitizer()
