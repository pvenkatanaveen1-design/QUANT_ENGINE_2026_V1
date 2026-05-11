"""
systems/intelligence/regime_detector.py — S8: Regime Detector.

WHY THIS FILE EXISTS
--------------------
Different market conditions require different strategies.
Trading a breakout strategy in a ranging market = constant stop-outs.
Trading a mean-reversion strategy in a trending market = fighting the trend.

The regime detector classifies the current market state so the strategy
selector can pick the right strategy for current conditions.

REGIMES DETECTED:
  STRONG_TREND:  ADX > 30.  Clear directional move.  Use breakout/pullback.
  WEAK_TREND:    ADX 20-30. Some direction but with pullbacks.  Use pullback.
  RANGE:         ADX < 20.  Sideways chop.  Use sweep strategies.
  HIGH_VOL:      ATR > 2× 20-bar average.  Too volatile — skip all trading.
  NEWS_CHAOS:    Manual flag set by news_guard.  Spreads unreliable.
  UNKNOWN:       Not enough data yet (startup or data gap).

CONFIDENCE SCORING (0.0 - 1.0):
  Calculated from:
    - How far ADX is from the nearest threshold (further = more confident)
    - ATR percentile rank (extreme values = more confident high_vol signal)
    - Indicator agreement (ADX + EMA alignment = higher confidence)
  Only trade when confidence >= 0.70 (set in RegimeState.is_tradeable()).

TREND DIRECTION:
  EMA(20) vs EMA(50) determines if the trend is BULLISH or BEARISH.
  Even in STRONG_TREND, if EMA(20) < EMA(50) = BEARISH.
  Strategy selectors use this to filter signal direction.

2026 XAUUSD REALITY:
  XAUUSD is heavily influenced by DXY (US Dollar Index) and US yields.
  A strong USD (DXY up) = bearish gold.  Weak USD = bullish gold.
  ADX alone doesn't capture this.  The trend direction from EMA captures it.
  For maximum accuracy: add macro feed context (S26) to regime scoring.

TRIGGER:
  Listens for CANDLE_CLOSED events (H1 candle closed from market_data_hub).
  Runs analysis on the last 100 H1 candles.
  Publishes REGIME_CHANGED only when regime actually changes.
  Also saves to regime_repository for dashboard history.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime
from typing import Optional

from core.constants import (
    ADX_STRONG_THRESH, ADX_TREND_THRESH, ADX_RANGE_THRESH,
    ATR_HIGH_VOL_MULT, ATR_LOOKBACK, ATR_PERIOD,
)
from core.enums import Regime, Session
from core.event_bus import EventType, bus
from core.logger import get_logger, LogCategory
from core.models.regime import RegimeState
from core.bus import get_value, set_value
from core import system_registry as reg
from market.features.adx import calculate_adx, calculate_di
from regime.classifiers.adx_classifier import classify_adx
from regime.classifiers.atr_classifier import classify_atr
from regime.classifiers.structure_classifier import classify_structure
from regime.classifiers.session_classifier import classify_session
from regime.classifiers.volume_classifier import classify_volume
from regime.classifiers.momentum_classifier import classify_momentum
from regime.regime_voter import vote_regime
from regime.regime_validator import validate_regime
from regime.transition_engine import detect_transition
from regime.strategy_mapping import map_regime_to_strategy
from regime.runtime_config import load_regime_runtime_config
from systems.data.market_data_hub import hub

log = get_logger("regime_detector", LogCategory.DATA)

# Number of H1 candles needed for reliable ADX(14) calculation
# ADX needs ~3× its period for warm-up.  100 gives plenty of history.
MIN_CANDLES_NEEDED = 50
REGIME_12 = (
    "TREND_LOW_VOL",
    "TREND_HIGH_VOL",
    "RANGE_LOW_VOL",
    "RANGE_HIGH_VOL",
    "BREAKOUT_EXPANSION",
    "NEWS_CHAOS",
    "ASIA_COMPRESSION",
    "NY_REVERSAL",
    "LIQUIDITY_SWEEP",
    "PULLBACK_CONTINUATION",
    "EXHAUSTION",
    "TRANSITION",
)
REGIME_TO_COARSE = {
    "TREND_LOW_VOL": Regime.WEAK_TREND,
    "TREND_HIGH_VOL": Regime.STRONG_TREND,
    "RANGE_LOW_VOL": Regime.RANGE,
    "RANGE_HIGH_VOL": Regime.HIGH_VOL,
    "BREAKOUT_EXPANSION": Regime.STRONG_TREND,
    "NEWS_CHAOS": Regime.NEWS_CHAOS,
    "ASIA_COMPRESSION": Regime.RANGE,
    "NY_REVERSAL": Regime.WEAK_TREND,
    "LIQUIDITY_SWEEP": Regime.RANGE,
    "PULLBACK_CONTINUATION": Regime.WEAK_TREND,
    "EXHAUSTION": Regime.HIGH_VOL,
    "TRANSITION": Regime.UNKNOWN,
}


class RegimeDetector:
    """
    Classifies current market regime and publishes RegimeState events.

    Subscribes to: CANDLE_CLOSED
    Publishes:     REGIME_CHANGED

    Singleton — import via:
        from systems.intelligence.regime_detector import regime_detector
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_regime: Optional[RegimeState] = None
        self._bars_in_regime: int = 0
        self._symbol   = "XAUUSD"
        self._timeframe = "H1"
        self._running  = False
        self._bars_since_last_flip = 9999

        # Try to load config thresholds (graceful fallback to constants)
        try:
            from core import config
            cfg = config.load("regimes")
            self._adx_strong = cfg.get("adx_strong_thresh", ADX_STRONG_THRESH)
            self._adx_trend  = cfg.get("adx_trend_thresh",  ADX_TREND_THRESH)
            self._adx_range  = cfg.get("adx_range_thresh",  ADX_RANGE_THRESH)
            self._atr_mult   = cfg.get("atr_high_vol_mult", ATR_HIGH_VOL_MULT)
        except Exception:
            self._adx_strong = ADX_STRONG_THRESH
            self._adx_trend  = ADX_TREND_THRESH
            self._adx_range  = ADX_RANGE_THRESH
            self._atr_mult   = ATR_HIGH_VOL_MULT

        log.info(
            f"RegimeDetector initialized — "
            f"ADX thresholds: range<{self._adx_range} "
            f"trend>{self._adx_trend} strong>{self._adx_strong}"
        )

    def start(self) -> None:
        """Subscribe to CANDLE_CLOSED events and start detecting."""
        bus.subscribe(EventType.CANDLE_CLOSED, self._on_candle_closed)
        self._running = True
        reg.update_system_status("regime_detector", "RUNNING")
        log.info("RegimeDetector started — listening for CANDLE_CLOSED events")

    def stop(self) -> None:
        bus.unsubscribe(EventType.CANDLE_CLOSED, self._on_candle_closed)
        self._running = False
        reg.update_system_status("regime_detector", "STOPPED")
        log.info("RegimeDetector stopped")

    # ─── EVENT HANDLER ────────────────────────────────────────────────────────

    def _on_candle_closed(self, event) -> None:
        """
        Called when a new H1 candle closes.  Runs classification.
        Payload: dict with symbol, timeframe, time, open, high, low, close, volume.
        """
        try:
            payload = event.payload
            if not isinstance(payload, dict):
                return

            symbol    = payload.get("symbol", "XAUUSD")
            timeframe = payload.get("timeframe", "H1")

            # Only process H1 for now (primary regime timeframe)
            if timeframe != "H1":
                log.debug(f"regime_detector | candle ignored timeframe={timeframe}")
                return

            log.info(f"regime_detector | H1 candle received | symbol={symbol}")
            self._classify(symbol, timeframe)

        except Exception as exc:
            log.error(f"RegimeDetector error on CANDLE_CLOSED: {exc}", exc_info=True)

    def _classify(self, symbol: str, timeframe: str) -> None:
        """
        Fetch candle data and run regime classification.
        Publishes REGIME_CHANGED if regime has changed.
        Saves to regime_repository regardless.
        """
        lookback_years = self._get_runtime_float("regime:lookback_years", 1.0)
        candles = self._load_candles(symbol, timeframe, lookback_years)

        # Handle both DataFrame and list formats
        try:
            if hasattr(candles, "empty"):  # pandas DataFrame
                if candles.empty or len(candles) < MIN_CANDLES_NEEDED:
                    csz = len(candles) if hasattr(candles, "__len__") else 0
                    reg.update_system_status("regime_detector", "WARNING", f"insufficient_h1_history:{csz}")
                    set_value("regime:waiting_reason", f"insufficient H1 history ({csz}/{MIN_CANDLES_NEEDED})")
                    log.info(f"regime_detector | insufficient candle history | symbol={symbol} candles={csz}")
                    return
                highs  = candles["high"].tolist()
                lows   = candles["low"].tolist()
                closes = candles["close"].tolist()
                volumes = candles["volume"].tolist() if "volume" in candles.columns else [0.0] * len(closes)
            else:  # list of dicts
                if len(candles) < MIN_CANDLES_NEEDED:
                    reg.update_system_status("regime_detector", "WARNING", f"insufficient_h1_history:{len(candles)}")
                    set_value("regime:waiting_reason", f"insufficient H1 history ({len(candles)}/{MIN_CANDLES_NEEDED})")
                    return
                highs  = [c["high"]  for c in candles]
                lows   = [c["low"]   for c in candles]
                closes = [c["close"] for c in candles]
                volumes = [float(c.get("volume", 0.0) or 0.0) for c in candles]
        except Exception as exc:
            log.error(f"Candle data extraction error: {exc}")
            return

        # ── Config-driven thresholds/weights ──────────────────────────────────
        rcfg = load_regime_runtime_config()

        # ── Calculate indicators ──────────────────────────────────────────────
        adx_val = calculate_adx(highs, lows, closes, period=ATR_PERIOD)
        pdi, ndi = calculate_di(highs, lows, closes, period=ATR_PERIOD)

        # ATR(14) — simple calculation
        atr_val = self._calculate_atr(highs, lows, closes, ATR_PERIOD)

        # ATR percentile vs last 20 bars
        atr_pct = self._calculate_atr_percentile(highs, lows, closes)
        momentum = classify_momentum(
            closes,
            overbought=float(rcfg["rsi"]["overbought"]),
            oversold=float(rcfg["rsi"]["oversold"]),
            neutral_low=float(rcfg["rsi"]["neutral_low"]),
            neutral_high=float(rcfg["rsi"]["neutral_high"]),
        )
        rsi_val = momentum.rsi_value
        vol_obj = classify_volume(
            volumes,
            spike_multiplier=float(rcfg["volume"]["spike_multiplier"]),
            dry_multiplier=float(rcfg["volume"]["dry_multiplier"]),
        )
        vol_sig = vol_obj.volume_signal
        struct_obj = classify_structure(
            highs,
            lows,
            closes,
            breakout_sensitivity=float(rcfg["structure"]["breakout_sensitivity"]),
            consolidation_length=int(rcfg["structure"]["consolidation_length"]),
            swing_lookback=int(rcfg["structure"]["swing_lookback"]),
        )
        struct = struct_obj.structure_label

        # EMA(20) and EMA(50) for trend direction
        ema_fast = self._calculate_ema(closes, 20)
        ema_slow = self._calculate_ema(closes, 50)

        if adx_val is None or atr_val is None:
            log.debug(f"Insufficient indicator data for {symbol}")
            return

        # ── Multi-factor classify regime ──────────────────────────────────────
        prev_adx = calculate_adx(highs[:-1], lows[:-1], closes[:-1], period=ATR_PERIOD) if len(closes) > ATR_PERIOD + 2 else None
        prev_atr = self._calculate_atr(highs[:-1], lows[:-1], closes[:-1], ATR_PERIOD) if len(closes) > ATR_PERIOD + 2 else None
        adx_cls = classify_adx(
            adx_val,
            prev_adx=prev_adx,
            weak_threshold=float(rcfg["adx"]["weak_trend_threshold"]),
            strong_threshold=float(rcfg["adx"]["strong_trend_threshold"]),
            no_trend_threshold=float(rcfg["adx"]["weak_trend_threshold"]) - 5.0,
        )
        atr_cls = classify_atr(
            atr_val,
            atr_pct,
            prev_atr=prev_atr,
            low_percentile=float(rcfg["atr"]["low_vol_percentile"]),
            high_percentile=float(rcfg["atr"]["high_vol_percentile"]),
            chaotic_percentile=float(rcfg["atr"]["chaotic_vol_percentile"]),
            expansion_change_pct=float(rcfg["atr"]["expansion_change_pct"]),
        )
        sess_cls = classify_session(
            asia_start_hour_ist=float(rcfg["session"]["asia_start_hour_ist"]),
            london_start_hour_ist=float(rcfg["session"]["london_start_hour_ist"]),
            newyork_start_hour_ist=float(rcfg["session"]["newyork_start_hour_ist"]),
            dst_offset_hours=float(rcfg["session"]["dst_offset_hours"]),
        )
        voted = vote_regime(
            adx_strength=adx_cls.trend_strength,
            atr_class=atr_cls.volatility_class,
            atr_expansion=atr_cls.expansion_state,
            structure_label=struct,
            session_behavior=sess_cls.behavior_label,
            volume_signal=vol_sig,
            momentum_label=momentum.momentum_label,
            weights={
                "adx": float(rcfg["probability"]["weight_adx"]),
                "atr": float(rcfg["probability"]["weight_atr"]),
                "structure": float(rcfg["probability"]["weight_structure"]),
                "session": float(rcfg["probability"]["weight_session"]),
                "volume": float(rcfg["probability"]["weight_volume"]),
                "momentum": float(rcfg["probability"]["weight_momentum"]),
            },
            probability_threshold=float(rcfg["probability"]["probability_threshold"]),
        )
        regime_label = voted.regime_label
        confidence = voted.confidence
        probs = voted.probabilities
        regime = REGIME_TO_COARSE.get(regime_label, Regime.UNKNOWN)

        # Trend direction from EMA alignment
        trend_dir = "NEUTRAL"
        if ema_fast and ema_slow:
            if ema_fast > ema_slow:
                trend_dir = "BULLISH"
            elif ema_fast < ema_slow:
                trend_dir = "BEARISH"

        # ── Get current session ───────────────────────────────────────────────
        try:
            from systems.intelligence.session_filter import session_filter
            session = session_filter.get_current_session()
        except Exception:
            session = Session.OFF

        # ── Build RegimeState ─────────────────────────────────────────────────
        with self._lock:
            previous = self._current_regime
            previous_regime_enum = previous.regime if previous else None
            regime_changed = (previous_regime_enum != regime)

            if regime_changed:
                self._bars_in_regime = 1
                self._bars_since_last_flip = 0
            else:
                self._bars_in_regime += 1
                self._bars_since_last_flip += 1

            bars = self._bars_in_regime

        transition = detect_transition(
            previous.regime_label if previous else None,
            regime_label,
            struct,
            atr_cls.expansion_state,
        )
        mapping = map_regime_to_strategy(
            regime_label,
            multipliers={
                "trend_size_multiplier": float(rcfg["strategy_mapping"]["trend_size_multiplier"]),
                "range_size_multiplier": float(rcfg["strategy_mapping"]["range_size_multiplier"]),
                "high_vol_size_multiplier": float(rcfg["strategy_mapping"]["high_vol_size_multiplier"]),
                "chaos_size_multiplier": float(rcfg["strategy_mapping"]["chaos_size_multiplier"]),
            },
        )
        validation = validate_regime(
            confidence=confidence,
            bars_in_regime=bars,
            min_confidence=float(rcfg["validator"]["confidence_threshold"]),
            min_bars=int(rcfg["validator"]["min_persistence_candles"]),
            flip_cooldown=int(rcfg["validator"]["regime_flip_cooldown"]),
            bars_since_last_flip=self._bars_since_last_flip,
        )
        if not validation.accepted:
            log.info(f"regime_validator | rejected_unstable_regime | reason={validation.reason}")

        new_state = RegimeState(
            symbol          = symbol,
            timeframe       = timeframe,
            regime          = regime,
            adx             = adx_val,
            atr             = atr_val,
            atr_percentile  = atr_pct,
            ema_fast        = ema_fast or 0.0,
            ema_slow        = ema_slow or 0.0,
            trend_direction = trend_dir,
            session         = session,
            confidence      = confidence,
            previous_regime = previous_regime_enum,
            regime_changed  = regime_changed,
            bars_in_regime  = bars,
            regime_label    = regime_label,
            probabilities   = probs,
            transition_state= transition,
            structure_label = struct,
            session_label   = sess_cls.session_label,
            rsi             = rsi_val or 0.0,
            volume_signal   = vol_sig,
            candles_used    = len(closes),
            lookback_years  = lookback_years,
            allowed_strategies = list(mapping.get("enabled", [])),
            mapping_reason  = self._mapping_reason(regime_label, confidence, bars),
            timestamp       = datetime.utcnow(),
        )

        with self._lock:
            self._current_regime = new_state

        # ── Save to repository ────────────────────────────────────────────────
        try:
            from repositories.regime_repository import RegimeRepository
            from services.storage_service import storage
            repo = RegimeRepository(storage)
            repo.insert(new_state)
        except Exception as exc:
            log.debug(f"Regime repo save error (non-critical): {exc}")

        # ── Publish if changed ────────────────────────────────────────────────
        if regime_changed:
            bus.publish(
                EventType.REGIME_CHANGED,
                new_state,
                source="regime_detector",
            )
            log.info(
                f"REGIME CHANGED: {symbol} "
                f"{previous_regime_enum.value if previous_regime_enum else 'NONE'} "
                f"→ {regime.value}/{regime_label} "
                f"ADX={adx_val:.1f} ATR={atr_val:.2f} RSI={rsi_val:.1f} "
                f"conf={confidence:.0%} bars={bars} transition={transition}"
            )
        else:
            log.debug(
                f"Regime: {symbol} {regime.value}/{regime_label} "
                f"ADX={adx_val:.1f} conf={confidence:.0%} bars={bars} transition={transition}"
            )

        bus.publish(EventType.REGIME_PROBABILITY, {
            "symbol": symbol,
            "timeframe": timeframe,
            "probabilities": probs,
            "regime_label": regime_label,
            "confidence": confidence,
            "timestamp": datetime.utcnow().isoformat(),
        }, source="regime_detector")
        bus.publish(EventType.REGIME_TRANSITION, {
            "symbol": symbol,
            "timeframe": timeframe,
            "transition_state": transition,
            "from": previous.regime_label if previous else None,
            "to": regime_label,
            "timestamp": datetime.utcnow().isoformat(),
        }, source="regime_detector")

        set_value("regime:current_label", regime_label, silent=True)
        set_value("regime:transition_state", transition, silent=True)
        set_value("regime:last_update_ts", datetime.utcnow().timestamp(), silent=True)
        set_value("regime:waiting_reason", "", silent=True)
        reg.touch_system_heartbeat("regime_detector")
        reg.update_system_status("regime_detector", "RUNNING")

    # ─── CLASSIFICATION LOGIC ─────────────────────────────────────────────────

    def _determine_regime_12(
        self,
        *,
        adx: float,
        atr_pct: float,
        ema_fast: Optional[float],
        ema_slow: Optional[float],
        structure: str,
        session: str,
        rsi: float,
        volume_signal: str,
    ) -> tuple[str, float, dict[str, float], str]:
        votes: dict[str, float] = defaultdict(float)
        transition = "STABLE"

        if adx >= self._adx_strong:
            votes["TREND_HIGH_VOL"] += 0.8 if atr_pct > 70 else 0.5
            votes["PULLBACK_CONTINUATION"] += 0.4
        elif adx >= self._adx_trend:
            votes["TREND_LOW_VOL"] += 0.6
            votes["PULLBACK_CONTINUATION"] += 0.5
        elif adx < self._adx_range:
            votes["RANGE_LOW_VOL"] += 0.7
            votes["LIQUIDITY_SWEEP"] += 0.4
        else:
            votes["TRANSITION"] += 0.5

        if atr_pct >= 95:
            votes["NEWS_CHAOS"] += 0.9
            votes["EXHAUSTION"] += 0.3
        elif atr_pct >= 80:
            votes["BREAKOUT_EXPANSION"] += 0.7
            votes["RANGE_HIGH_VOL"] += 0.4
        elif atr_pct < 30:
            votes["ASIA_COMPRESSION"] += 0.6
            votes["RANGE_LOW_VOL"] += 0.3

        if structure == "BREAKOUT":
            votes["BREAKOUT_EXPANSION"] += 0.8
        elif structure == "RANGE":
            votes["RANGE_LOW_VOL"] += 0.5
            votes["LIQUIDITY_SWEEP"] += 0.6
        elif structure == "COMPRESSION":
            votes["ASIA_COMPRESSION"] += 0.7
            transition = "COMPRESSION_TO_EXPANSION"
        elif structure == "EXHAUSTION":
            votes["EXHAUSTION"] += 0.9
            transition = "TREND_TO_RANGE"
        elif structure == "CONTINUATION":
            votes["PULLBACK_CONTINUATION"] += 0.7

        if session == "ASIA":
            votes["ASIA_COMPRESSION"] += 0.4
        elif session in ("NEW_YORK", "OVERLAP"):
            votes["NY_REVERSAL"] += 0.4 if rsi > 70 or rsi < 30 else 0.2
            votes["BREAKOUT_EXPANSION"] += 0.2

        if volume_signal == "SPIKE":
            votes["BREAKOUT_EXPANSION"] += 0.4
            votes["NEWS_CHAOS"] += 0.2
        elif volume_signal == "DRY":
            votes["RANGE_LOW_VOL"] += 0.3

        if ema_fast and ema_slow and abs(ema_fast - ema_slow) < 1e-9:
            votes["TRANSITION"] += 0.6

        total = sum(max(0.0, v) for v in votes.values()) or 1.0
        probs = {k: round(v / total, 4) for k, v in votes.items()}
        for label in REGIME_12:
            probs.setdefault(label, 0.0)
        top = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
        winner, winner_p = top[0]
        runner_p = top[1][1] if len(top) > 1 else 0.0
        confidence = max(0.0, min(1.0, winner_p - runner_p + 0.5))
        if confidence < 0.55:
            winner = "TRANSITION"
            transition = "UNSTABLE_VOTES"
        return winner, round(confidence, 2), probs, transition

    def _classify_structure(self, highs: list[float], lows: list[float], closes: list[float]) -> str:
        if len(closes) < 20:
            return "UNKNOWN"
        hi = max(highs[-20:-1])
        lo = min(lows[-20:-1])
        rng = hi - lo
        last = closes[-1]
        prev = closes[-2]
        if rng > 0 and last > hi:
            return "BREAKOUT"
        if rng > 0 and last < lo:
            return "BREAKOUT"
        if rng > 0 and (rng / max(1e-9, closes[-1])) < 0.002:
            return "COMPRESSION"
        if abs(last - prev) < (rng * 0.06):
            return "RANGE"
        if len(closes) >= 6 and closes[-1] > closes[-3] > closes[-6]:
            return "CONTINUATION"
        if len(closes) >= 6 and closes[-1] < closes[-3] < closes[-6]:
            return "CONTINUATION"
        if len(closes) >= 4 and ((closes[-1] > closes[-2] < closes[-3]) or (closes[-1] < closes[-2] > closes[-3])):
            return "EXHAUSTION"
        return "TREND"

    def _classify_volume(self, volumes: list[float]) -> str:
        if not volumes:
            return "UNKNOWN"
        if len(volumes) < 21:
            return "NORMAL"
        avg = sum(volumes[-21:-1]) / 20
        cur = volumes[-1]
        if avg <= 0:
            return "NORMAL"
        ratio = cur / avg
        if ratio >= 1.8:
            return "SPIKE"
        if ratio <= 0.6:
            return "DRY"
        return "NORMAL"

    def _calculate_rsi(self, closes: list[float], period: int = 14) -> float:
        if len(closes) <= period:
            return 50.0
        gains = []
        losses = []
        for i in range(-period, 0):
            delta = closes[i] - closes[i - 1]
            gains.append(max(delta, 0.0))
            losses.append(abs(min(delta, 0.0)))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    def _session_label(self) -> str:
        try:
            from systems.intelligence.session_filter import session_filter
            s = session_filter.get_current_session()
            return s.value if hasattr(s, "value") else str(s)
        except Exception:
            return "OFF"

    def _allowed_strategies_for_regime(self, regime_label: str) -> list[str]:
        mapping = {
            "TREND_LOW_VOL": ["alpha_pullback"],
            "TREND_HIGH_VOL": ["alpha_breakout", "alpha_pullback"],
            "RANGE_LOW_VOL": ["alpha_sweep"],
            "RANGE_HIGH_VOL": ["alpha_sweep"],
            "BREAKOUT_EXPANSION": ["alpha_breakout"],
            "NEWS_CHAOS": [],
            "ASIA_COMPRESSION": [],
            "NY_REVERSAL": ["alpha_sweep"],
            "LIQUIDITY_SWEEP": ["alpha_sweep"],
            "PULLBACK_CONTINUATION": ["alpha_pullback"],
            "EXHAUSTION": [],
            "TRANSITION": [],
        }
        return list(mapping.get(regime_label, []))

    def _mapping_reason(self, regime_label: str, confidence: float, bars: int) -> str:
        if regime_label in ("NEWS_CHAOS", "TRANSITION", "ASIA_COMPRESSION", "EXHAUSTION"):
            return "Regime marked non-tradeable: wait for stability/normalization."
        if confidence < 0.70:
            return "Confidence below trade threshold (0.70)."
        if bars < 3:
            return "Persistence filter: wait for at least 3 bars in regime."
        return "Regime supports mapped strategies."

    def _load_candles(self, symbol: str, timeframe: str, lookback_years: float):
        try:
            from services.storage_service import storage
            days = max(7, int(365 * lookback_years))
            rows = storage.execute_duckdb(
                """
                SELECT time, open, high, low, close, volume
                FROM candles
                WHERE symbol = ? AND timeframe = ? AND time >= CURRENT_TIMESTAMP - INTERVAL ? DAY
                ORDER BY time ASC
                """,
                (symbol, timeframe, days),
            )
            if rows:
                return [
                    {
                        "time": r[0],
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5] or 0.0),
                    }
                    for r in rows
                ]
        except Exception as exc:
            log.debug(f"regime_detector | duckdb history read fallback | {exc}")
        return hub.get_candles(symbol, timeframe, n=1000)

    def _get_runtime_float(self, key: str, default: float) -> float:
        raw = get_value(key, silent=True)
        if raw is None:
            return default
        try:
            return float(raw)
        except Exception:
            return default

    # ─── INDICATOR CALCULATIONS ───────────────────────────────────────────────

    def _calculate_atr(
        self,
        highs:  list[float],
        lows:   list[float],
        closes: list[float],
        period: int = 14,
    ) -> Optional[float]:
        """Calculate ATR(period) using Wilder smoothing."""
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            hl  = highs[i] - lows[i]
            hpc = abs(highs[i] - closes[i - 1])
            lpc = abs(lows[i]  - closes[i - 1])
            trs.append(max(hl, hpc, lpc))
        if len(trs) < period:
            return None
        # Wilder smoothing
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return round(atr, 4)

    def _calculate_atr_percentile(
        self,
        highs:  list[float],
        lows:   list[float],
        closes: list[float],
    ) -> float:
        """
        Calculate ATR percentile vs last ATR_LOOKBACK ATRs.
        0 = lowest vol in lookback, 100 = highest vol in lookback.
        Used to detect HIGH_VOL regime (ATR spike).
        """
        lookback = ATR_LOOKBACK
        if len(closes) < ATR_PERIOD + lookback + 1:
            return 50.0  # default to mid-range if insufficient data

        # Calculate ATR for each of the last lookback+1 windows
        atrs = []
        for offset in range(lookback + 1):
            end = len(closes) - offset
            if end < ATR_PERIOD + 1:
                break
            h = highs[:end]
            l = lows[:end]
            c = closes[:end]
            a = self._calculate_atr(h, l, c, ATR_PERIOD)
            if a:
                atrs.append(a)

        if len(atrs) < 2:
            return 50.0

        current_atr = atrs[0]
        all_atrs    = atrs[1:]  # historical ATRs (not including current)
        below_count = sum(1 for a in all_atrs if a < current_atr)
        return round(below_count / len(all_atrs) * 100, 1)

    def _calculate_ema(self, closes: list[float], period: int) -> Optional[float]:
        """Calculate Exponential Moving Average for most recent bar."""
        if len(closes) < period:
            return None
        k   = 2.0 / (period + 1)
        ema = sum(closes[:period]) / period  # SMA seed
        for price in closes[period:]:
            ema = price * k + ema * (1 - k)
        return round(ema, 4)

    # ─── PUBLIC ACCESS ────────────────────────────────────────────────────────

    def get_current_regime(self) -> Optional[RegimeState]:
        """
        Return the most recent RegimeState.
        Returns None if no candle has been processed yet (startup).
        Used by strategies before generating signals.
        """
        with self._lock:
            return self._current_regime

    def is_tradeable(self) -> bool:
        """
        Quick check: is the market in a tradeable regime?
        Returns False on startup (no data) or in HIGH_VOL, NEWS_CHAOS, UNKNOWN.
        """
        with self._lock:
            r = self._current_regime
        return r.is_tradeable() if r else False


# ─── SINGLETON ────────────────────────────────────────────────────────────────
regime_detector = RegimeDetector()
