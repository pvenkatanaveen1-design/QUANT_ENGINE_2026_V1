"""
Multi-voter regime detection (M15 + H1). No Redis.

Loads thresholds from config/regimes.yaml. Uses pandas-ta for indicators.

Integrates ``regime.classifiers.ensemble_regime`` as optional cross-check (ensemble_hint).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

log = logging.getLogger(__name__)

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ENGINE_ROOT / "config" / "regimes.yaml"
_STATE_DIR = _ENGINE_ROOT / "state"
_HISTORY_PATH = _STATE_DIR / "regime_history.json"

IST = timezone(timedelta(hours=5, minutes=30))

_DEFAULT_CFG: dict[str, Any] = {
    "thresholds": {
        "adx_trend_min": 30,
        "adx_range_max": 22,
        "atr_high_threshold": 1.3,
        "rsi_trend_min": 48,
        "min_confidence_to_trade": 60,
        "min_regime_duration": 3,
    },
    "sessions": {
        "london_kill_zone_start_ist": "02:00",
        "london_kill_zone_end_ist": "05:00",
        "ny_kill_zone_start_ist": "12:00",
        "ny_kill_zone_end_ist": "14:00",
        "asian_dead_start_ist": "22:00",
        "asian_dead_end_ist": "01:30",
    },
}


def _load_regime_yaml() -> dict[str, Any]:
    try:
        if not _CONFIG_PATH.exists():
            log.warning("config/regimes.yaml not found — using built-in defaults")
            return json.loads(json.dumps(_DEFAULT_CFG))
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        merged = {**_DEFAULT_CFG, **{k: v for k, v in data.items() if v is not None}}
        if "thresholds" in data and isinstance(data["thresholds"], dict):
            merged["thresholds"] = {**_DEFAULT_CFG["thresholds"], **data["thresholds"]}
        if "sessions" in data and isinstance(data["sessions"], dict):
            merged["sessions"] = {**_DEFAULT_CFG["sessions"], **data["sessions"]}
        return merged
    except Exception as exc:
        log.warning("Failed loading regimes.yaml: %s — using defaults", exc)
        return json.loads(json.dumps(_DEFAULT_CFG))


def _parse_hm(s: str) -> dt_time:
    parts = str(s).strip().split(":")
    h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    return dt_time(h, m)


def _time_in_range_ist(t: dt_time, start: dt_time, end: dt_time) -> bool:
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def _voter_session(cfg: dict, now_ist_time: dt_time | None = None) -> str:
    sess = cfg.get("sessions") or _DEFAULT_CFG["sessions"]
    now = now_ist_time if now_ist_time is not None else datetime.now(IST).time()
    london_s = _parse_hm(sess.get("london_kill_zone_start_ist", "02:00"))
    london_e = _parse_hm(sess.get("london_kill_zone_end_ist", "05:00"))
    ny_s = _parse_hm(sess.get("ny_kill_zone_start_ist", "12:00"))
    ny_e = _parse_hm(sess.get("ny_kill_zone_end_ist", "14:00"))
    asia_s = _parse_hm(sess.get("asian_dead_start_ist", "22:00"))
    asia_e = _parse_hm(sess.get("asian_dead_end_ist", "01:30"))

    if _time_in_range_ist(now, asia_s, asia_e):
        return "DEAD_SESSION"
    if _time_in_range_ist(now, london_s, london_e) or _time_in_range_ist(now, ny_s, ny_e):
        return "ACTIVE_SESSION"
    return "NORMAL_SESSION"


def _voter_structure(df_h1: pd.DataFrame) -> str:
    if df_h1 is None or len(df_h1) < 21:
        return "RANGE"
    tail = df_h1.tail(20)
    highs = tail["high"].astype(float).tolist()
    lows = tail["low"].astype(float).tolist()
    hh = hl = lh = ll = 0
    for i in range(1, len(highs)):
        if highs[i] > highs[i - 1]:
            hh += 1
        elif highs[i] < highs[i - 1]:
            lh += 1
        if lows[i] > lows[i - 1]:
            hl += 1
        elif lows[i] < lows[i - 1]:
            ll += 1
    bull = hh + hl
    bear = lh + ll
    if bull >= 6 and hh >= 3 and hl >= 3:
        return "TREND"
    if bear >= 6 and lh >= 3 and ll >= 3:
        return "TREND"
    return "RANGE"


def _voter_indicator(adx: float, atr_ratio: float, thr: dict) -> str:
    adx_min = float(thr.get("adx_trend_min", 30))
    adx_max = float(thr.get("adx_range_max", 22))
    atr_hi = float(thr.get("atr_high_threshold", 1.3))
    if adx > adx_min and atr_ratio < atr_hi:
        return "TREND_LOW"
    if adx > adx_min and atr_ratio >= atr_hi:
        return "TREND_HIGH"
    if adx < adx_max and atr_ratio < atr_hi:
        return "RANGE_LOW"
    return "TRANSITION"


def _update_streak_disk(label: str) -> int:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if _HISTORY_PATH.exists():
            data = json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
        if data.get("last_label") == label:
            streak = int(data.get("streak", 0)) + 1
        else:
            streak = 1
        data.update({"last_label": label, "streak": streak, "updated": datetime.now(timezone.utc).isoformat()})
        _HISTORY_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return streak
    except Exception as exc:
        log.warning("regime streak update failed: %s", exc)
        return 1


def _update_streak_memory(label: str, mem: dict[str, Any]) -> int:
    """In-memory streak for walk-forward / backtest (same ``mem`` dict each bar)."""
    if mem.get("last_label") == label:
        streak = int(mem.get("streak", 0)) + 1
    else:
        streak = 1
    mem["last_label"] = label
    mem["streak"] = streak
    return streak


def _transition_warning(adx: float, atr_ratio: float, prev_ratio: float | None, thr: dict) -> bool:
    lo = float(thr.get("adx_range_max", 22))
    hi = float(thr.get("adx_trend_min", 30))
    if lo < adx < hi:
        return True
    if prev_ratio is not None and prev_ratio > 0 and atr_ratio > prev_ratio * 1.3:
        return True
    return False


def _confidence_from_votes(trend_votes: int, range_votes: int, transition_flag: bool) -> float:
    if transition_flag:
        return 35.0
    m = max(trend_votes, range_votes)
    if m >= 3:
        return 92.0
    if m == 2:
        return 72.0
    if m == 1:
        return 45.0
    return 20.0


def detect_regime(
    df_m15: pd.DataFrame,
    df_h1: pd.DataFrame,
    *,
    reference_time_utc: datetime | None = None,
    persist_regime_history: bool = True,
    regime_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build regime dict: label Q1|Q2|Q3|Q4|SKIP, confidence, duration_candles,
    transition_warning, metrics, voters, ensemble_hint.

    ``reference_time_utc``: bar close time for backtests — session voter uses IST slice of this
    instead of wall-clock now. Live usage: leave default None.

    ``persist_regime_history``: if False, do not write ``state/regime_history.json``;
    pass ``regime_memory`` (reuse same dict across bars) for duration_candles in walk-forward.
    """
    try:
        import pandas_ta as ta
    except ImportError as exc:
        raise RuntimeError("pandas-ta required: pip install pandas-ta") from exc

    cfg = _load_regime_yaml()
    thr = cfg.get("thresholds") or _DEFAULT_CFG["thresholds"]

    if df_m15 is None or len(df_m15) < 30:
        raise ValueError("M15 dataframe too short for regime detection")
    if df_h1 is None or len(df_h1) < 60:
        raise ValueError("H1 dataframe too short for regime detection")

    h1 = df_h1.copy()
    m15 = df_m15.copy()
    for col in ("open", "high", "low", "close"):
        h1[col] = h1[col].astype(float)
        m15[col] = m15[col].astype(float)
    h1["volume"] = h1["volume"].astype(float)
    m15["volume"] = m15["volume"].astype(float)

    adx_df = ta.adx(h1["high"], h1["low"], h1["close"], length=14)
    if adx_df is None or adx_df.empty:
        adx = 0.0
    else:
        adx_cols = [c for c in adx_df.columns if str(c).upper().startswith("ADX")]
        adx = float(adx_df[adx_cols[0]].iloc[-1]) if adx_cols else float(adx_df.iloc[-1, -1])

    atr_m = ta.atr(m15["high"], m15["low"], m15["close"], length=14)
    atr_series = atr_m.iloc[:, 0].astype(float)
    atr_cur = float(atr_series.iloc[-1])
    atr_mean20 = float(atr_series.tail(20).mean())
    atr_ratio = atr_cur / atr_mean20 if atr_mean20 > 1e-12 else 1.0
    prev_ratio = None
    if len(atr_series) >= 2:
        pm = float(atr_series.iloc[-22:-2].mean()) if len(atr_series) >= 22 else float(atr_series.iloc[-2])
        if pm > 1e-12:
            prev_ratio = float(atr_series.iloc[-2]) / pm

    rsi_m = ta.rsi(m15["close"], length=14)
    rsi_col = rsi_m.columns[0]
    rsi = float(rsi_m[rsi_col].iloc[-1])

    ema21 = ta.ema(h1["close"], length=21)
    ema50 = ta.ema(h1["close"], length=50)
    ema21_v = float(ema21.iloc[-1, 0])
    ema50_v = float(ema50.iloc[-1, 0])

    bb = ta.bbands(h1["close"], length=20)
    bbl, bbm, bbu = bb.columns[0], bb.columns[1], bb.columns[2]
    bw = float((bb[bbu].iloc[-1] - bb[bbl].iloc[-1]) / max(abs(bb[bbm].iloc[-1]), 1e-12))

    v1 = _voter_indicator(adx, atr_ratio, thr)
    v2 = _voter_structure(h1)
    if reference_time_utc is not None:
        rt = reference_time_utc
        if rt.tzinfo is None:
            rt = rt.replace(tzinfo=timezone.utc)
        ist_time = rt.astimezone(IST).time()
        v3 = _voter_session(cfg, ist_time)
    else:
        v3 = _voter_session(cfg)

    trend_votes = 0
    range_votes = 0
    if v3 == "DEAD_SESSION":
        label = "SKIP"
    else:
        trend_votes = sum(
            [
                1 if v1 in ("TREND_LOW", "TREND_HIGH") else 0,
                1 if v2 == "TREND" else 0,
            ]
        )
        range_votes = sum(
            [
                1 if v1 == "RANGE_LOW" else 0,
                1 if v2 == "RANGE" else 0,
            ]
        )
        atr_hdr = float(thr.get("atr_high_threshold", 1.3))
        if v1 == "TRANSITION":
            label = "Q4"
        elif trend_votes >= 2 and atr_ratio < atr_hdr:
            label = "Q1"
        elif trend_votes >= 2 and atr_ratio >= atr_hdr:
            label = "Q2"
        elif range_votes >= 2:
            label = "Q3"
        else:
            label = "Q4"

    trans_warn = _transition_warning(adx, atr_ratio, prev_ratio, thr)
    conf = _confidence_from_votes(trend_votes if label != "SKIP" else 0, range_votes if label != "SKIP" else 0, v1 == "TRANSITION")
    if label == "SKIP":
        conf = min(conf, 40.0)
    if persist_regime_history:
        duration = _update_streak_disk(label)
    else:
        if regime_memory is None:
            log.warning(
                "detect_regime(..., persist_regime_history=False) without regime_memory — "
                "duration_candles resets each call"
            )
        mem: dict[str, Any] = regime_memory if regime_memory is not None else {}
        duration = _update_streak_memory(label, mem)
    min_dur = int(thr.get("min_regime_duration", 3))
    if duration < min_dur:
        conf = min(conf, float(thr.get("min_confidence_to_trade", 60)) - 1.0)

    ensemble_hint = ""
    try:
        from regime.classifiers.ensemble_regime import EnsembleRegimeClassifier

        clf = EnsembleRegimeClassifier()
        hi = h1["high"].astype(float).tolist()
        lo = h1["low"].astype(float).tolist()
        cl = h1["close"].astype(float).tolist()
        ens_t = reference_time_utc if reference_time_utc is not None else datetime.now(timezone.utc)
        if ens_t.tzinfo is None:
            ens_t = ens_t.replace(tzinfo=timezone.utc)
        ens = clf.classify(hi, lo, cl, time_utc=ens_t)
        ensemble_hint = str(ens.label.value)
    except Exception as exc:
        log.debug("ensemble regime optional: %s", exc)

    return {
        "label": label,
        "confidence": round(float(conf), 2),
        "duration_candles": int(duration),
        "transition_warning": bool(trans_warn),
        "metrics": {
            "adx_h1": round(adx, 2),
            "atr_m15": round(atr_cur, 6),
            "atr_ratio": round(atr_ratio, 4),
            "rsi_m15": round(rsi, 2),
            "ema21_h1": round(ema21_v, 5),
            "ema50_h1": round(ema50_v, 5),
            "bb_width_h1": round(bw, 4),
        },
        "voters": {
            "indicator": v1,
            "structure": v2,
            "session": v3,
        },
        "ensemble_hint": ensemble_hint,
    }
