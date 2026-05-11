from __future__ import annotations

import logging
import os
import sys
import time
from collections import deque
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.bus import RedisBus  # noqa: E402
from core.clock import utc_now  # noqa: E402
from core.constants import CHANNEL_SIGNALS_RAW, CHANNEL_TICKS_PATTERN  # noqa: E402
from core.tick_sanitizer import sanitize_tick_dict  # noqa: E402
from features.atr import atr_rma  # noqa: E402
from qualifiers.news_filter import passes_news_gate  # noqa: E402
from qualifiers.session_filter import passes_session  # noqa: E402
from qualifiers.spread_filter import spread_ok  # noqa: E402
from regime.classifiers.ensemble_regime import EnsembleRegimeClassifier  # noqa: E402
from risk.position_sizer import load_pair_settings  # noqa: E402
from schemas.tick import Tick  # noqa: E402
from signals.alpha.alpha_sweep import maybe_generate_signal  # noqa: E402
from signals.liquidity.asian_range import asian_hi_lo  # noqa: E402
from signals.liquidity.pdh_pdl_mapper import prior_range_from_mids  # noqa: E402
from signals.scoring.confluence_score import score_signal  # noqa: E402

_LOG = logging.getLogger(__name__)


class _BarBuilder:
    def __init__(self, chunk: int, mids_cap: int) -> None:
        self.chunk = chunk
        self.mids_cap = mids_cap
        self.buf: list[tuple[float, float, float]] = []
        self.highs: list[float] = []
        self.lows: list[float] = []
        self.closes: list[float] = []
        self.mids: deque[float] = deque()

    def ingest_tick(self, tick: Tick) -> None:
        mid = (tick.bid + tick.ask) / 2.0
        self.mids.append(mid)
        self.buf.append((tick.bid, tick.ask, mid))
        while len(self.mids) > self.mids_cap:
            self.mids.popleft()
        if len(self.buf) >= self.chunk:
            hi_bid = max(t[0] for t in self.buf)
            hi_ask = max(t[1] for t in self.buf)
            lo_bid = min(t[0] for t in self.buf)
            lo_ask = min(t[1] for t in self.buf)
            self.highs.append(max(hi_bid, hi_ask))
            self.lows.append(min(lo_bid, lo_ask))
            self.closes.append(float(self.buf[-1][2]))
            self.buf.clear()
            tail = max(2600, int(os.environ.get("QUANT_PRICE_HISTORY", "2600")))
            if len(self.highs) > tail:
                self.highs = self.highs[-tail:]
                self.lows = self.lows[-tail:]
                self.closes = self.closes[-tail:]


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    load_dotenv()
    symbol_default = os.environ.get("SYMBOL_DEFAULT", "EURUSD")
    bar_chunk = int(os.environ.get("QUANT_BAR_CHUNK", "140"))
    prior_mid_window = int(os.environ.get("QUANT_PRIOR_DAY_MIDS_WINDOW", "12000"))
    asian_bars = int(os.environ.get("QUANT_ASIAN_BARS", "80"))
    min_conf = float(os.environ.get("QUANT_MIN_CONFLUENCE", "58"))
    cooldown = float(os.environ.get("QUANT_SIGNAL_COOLDOWN_SEC", "600"))
    mids_cap = int(os.environ.get("QUANT_MAX_MIDS", "200000"))
    pairs = load_pair_settings()
    builder = _BarBuilder(chunk=bar_chunk, mids_cap=mids_cap)
    regime_model = EnsembleRegimeClassifier()

    last_sent = 0.0
    bus = RedisBus.from_env()

    for channel, payload in bus.iter_pattern_messages(CHANNEL_TICKS_PATTERN):
        if not channel.endswith(f":{symbol_default}"):
            continue
        clean = sanitize_tick_dict(dict(payload))
        tick = Tick.from_dict(clean)
        bus.set_str(f"quant:last_mid:{tick.symbol}", str((tick.bid + tick.ask) / 2), ex=30)
        bus.set_str(f"quant:last_quote:{tick.symbol}", f"{tick.bid}:{tick.ask}:{tick.time_utc.isoformat()}", ex=30)

        builder.ingest_tick(tick)

        highs, lows, closes = builder.highs, builder.lows, builder.closes
        if len(closes) < int(os.environ.get("QUANT_MIN_BARS", "160")):
            continue

        now_ts = time.time()
        if now_ts - last_sent < cooldown:
            continue

        regime = regime_model.classify(highs, lows, closes, time_utc=utc_now())
        atr_cur = atr_rma(highs, lows, closes, period=int(pairs.get(symbol_default, {}).get("atr_period", 14)))
        rng_lvls = prior_range_from_mids(builder.mids, window=prior_mid_window)
        as_hi, as_lo = asian_hi_lo(builder.highs, builder.lows, asian_len=int(asian_bars))

        highs_ref = rng_lvls.prior_day_high if rng_lvls.prior_day_high else as_hi
        lows_ref = rng_lvls.prior_day_low if rng_lvls.prior_day_low else as_lo
        sweep_high = highs_ref if highs_ref else max(builder.highs[-50:])
        sweep_low = lows_ref if lows_ref else min(builder.lows[-50:])
        last_mid = (tick.bid + tick.ask) / 2.0

        pair_cfg = pairs.get(symbol_default, {})
        session_ok_flag = passes_session(tick.time_utc)
        news_ok_flag = passes_news_gate(symbol_default)
        spread_ok_flag, spread_val = spread_ok(symbol_default, tick.bid, tick.ask, pair_cfg)

        if not spread_ok_flag or not session_ok_flag or not news_ok_flag:
            continue

        score = score_signal(regime, spread_ok_flag, session_ok_flag)
        cand = maybe_generate_signal(symbol_default, regime, last_mid, sweep_high, sweep_low)
        if cand is None:
            continue
        cand.confluence_score = float(score)

        atr_val = atr_cur if atr_cur is not None else float(os.environ.get("QUANT_FALLBACK_ATR_PRICE", "0.00055"))
        cand.extras["atr_estimate"] = float(atr_val)
        cand.extras["spread_pips_estimate"] = float(spread_val)
        cand.extras["regime"] = regime.label.value

        if cand.confluence_score < min_conf:
            continue

        bus.publish_json(CHANNEL_SIGNALS_RAW, cand.to_dict())
        last_sent = now_ts
        bus.heartbeat("brain")
        _LOG.info(
            "signal_emitted dir=%s score=%s regime=%s",
            cand.direction,
            cand.confluence_score,
            regime.label.value,
        )


if __name__ == "__main__":
    main()
