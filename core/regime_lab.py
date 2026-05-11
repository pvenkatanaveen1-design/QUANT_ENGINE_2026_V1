"""
Historical occurrence study for taxonomy regimes (MT5 OHLC).

Uses rule proxies in ``regime_taxonomy.yaml``. Many regimes are marked not detectable
from OHLC alone (macro, sentiment, COT) — those return a skip result.
"""

from __future__ import annotations

import logging
from datetime import time as dtime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def _structure_bias_h1(df_h1: pd.DataFrame) -> str:
    if df_h1 is None or len(df_h1) < 21:
        return "mixed"
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
        return "bull"
    if bear >= 6 and lh >= 3 and ll >= 3:
        return "bear"
    return "mixed"


def _in_ist_range(t_utc: Any, start_hm: tuple[int, int], end_hm: tuple[int, int]) -> bool:
    tt = pd.Timestamp(t_utc)
    if tt.tzinfo is None:
        tt = tt.tz_localize("UTC")
    t = tt.tz_convert(IST).time()
    a = dtime(start_hm[0], start_hm[1])
    b = dtime(end_hm[0], end_hm[1])
    if a <= b:
        return a <= t <= b
    return t >= a or t <= b


def _session_kill_zone_ist(t_utc: Any) -> bool:
    return _in_ist_range(t_utc, (2, 0), (5, 0)) or _in_ist_range(t_utc, (12, 0), (14, 0))


def _session_asian_range_ist(t_utc: Any) -> bool:
    return _in_ist_range(t_utc, (22, 0), (23, 59)) or _in_ist_range(t_utc, (0, 0), (2, 0))


def _wick_sweep_last_n(m15: pd.DataFrame, n: int = 5) -> bool:
    if len(m15) < n + 2:
        return False
    seg = m15.tail(n).copy()
    hi_prev = float(m15["high"].iloc[-n - 1])
    lo_prev = float(m15["low"].iloc[-n - 1])
    for i in range(len(seg)):
        row = seg.iloc[i]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        if h > hi_prev and c < hi_prev and max(o, c) < h - 1e-9:
            return True
        if l < lo_prev and c > lo_prev and min(o, c) > l + 1e-9:
            return True
    return False


def _ensure_h1_bbw(df_h1: pd.DataFrame) -> pd.DataFrame:
    if "bbw" in df_h1.columns:
        return df_h1
    import pandas_ta as ta

    h = df_h1.copy()
    c = h["close"].astype(float)
    bb = ta.bbands(c, length=20)
    bbl, bbm, bbu = bb.columns[0], bb.columns[1], bb.columns[2]
    h["bbw"] = (bb[bbu].astype(float) - bb[bbl].astype(float)) / bb[bbm].astype(float).abs().clip(lower=1e-12)
    return h


def build_aligned_features(df_m15: pd.DataFrame, df_h1: pd.DataFrame) -> pd.DataFrame:
    import pandas_ta as ta

    m = df_m15.sort_values("time").reset_index(drop=True).copy()
    h = df_h1.sort_values("time").reset_index(drop=True).copy()
    for col in ("open", "high", "low", "close"):
        m[col] = m[col].astype(float)
        h[col] = h[col].astype(float)
    rsi = ta.rsi(m["close"], length=14)
    m["rsi"] = rsi.iloc[:, 0].astype(float)
    atr = ta.atr(m["high"], m["low"], m["close"], length=14)
    atr0 = atr.iloc[:, 0].astype(float)
    m["atr_ratio"] = atr0 / atr0.rolling(20).mean().replace(0, np.nan)
    m["ret_1"] = m["close"].pct_change()
    adx_df = ta.adx(h["high"], h["low"], h["close"], length=14)
    adx_cols = [c for c in adx_df.columns if str(c).upper().startswith("ADX")]
    h["adx"] = adx_df[adx_cols[0]].astype(float) if adx_cols else 0.0
    h["ema50"] = ta.ema(h["close"], length=50).iloc[:, 0].astype(float)
    bb = ta.bbands(h["close"], length=20)
    bbl, bbm, bbu = bb.columns[0], bb.columns[1], bb.columns[2]
    h["bbw"] = (bb[bbu].astype(float) - bb[bbl].astype(float)) / bb[bbm].astype(float).abs().clip(lower=1e-12)
    feat_h = h[["time", "adx", "ema50", "bbw"]].dropna(subset=["adx"])
    out = pd.merge_asof(m, feat_h, on="time", direction="backward")
    out["zscore"] = (out["close"] - out["close"].rolling(20).mean()) / out["close"].rolling(20).std().replace(0, np.nan)
    return out


def _match_row(
    rules: dict[str, Any],
    *,
    m15_upto: pd.DataFrame,
    h1_upto: pd.DataFrame,
    row: pd.Series,
    prev_row: pd.Series | None,
) -> bool:
    try:
        if not rules or "note" in rules:
            return False
        adx = float(row.get("adx", np.nan))
        rsi = float(row.get("rsi", np.nan))
        close = float(row["close"])
        ema50 = float(row.get("ema50", np.nan))
        atrr = float(row.get("atr_ratio", np.nan))
        z = float(row.get("zscore", np.nan))
        t = row["time"]

        if "min_adx_h1" in rules and not np.isnan(adx) and adx < float(rules["min_adx_h1"]):
            return False
        if "max_adx_h1" in rules and not np.isnan(adx) and adx > float(rules["max_adx_h1"]):
            return False
        if "min_rsi_m15" in rules and not np.isnan(rsi) and rsi < float(rules["min_rsi_m15"]):
            return False
        if "max_rsi_m15" in rules and not np.isnan(rsi) and rsi > float(rules["max_rsi_m15"]):
            return False
        if "min_atr_ratio" in rules and not np.isnan(atrr) and atrr < float(rules["min_atr_ratio"]):
            return False
        if "max_atr_ratio" in rules and not np.isnan(atrr) and atrr > float(rules["max_atr_ratio"]):
            return False

        bias = _structure_bias_h1(h1_upto)
        st_h1 = rules.get("structure_h1")
        if st_h1 == "any_trend":
            if bias == "mixed" and (np.isnan(adx) or adx < 22):
                return False
        elif st_h1 == "bull":
            if bias != "bull":
                return False
        elif st_h1 == "bear":
            if bias != "bear":
                return False

        if rules.get("close_vs_ema50_h1") == "above" and not np.isnan(ema50) and close < ema50:
            return False
        if rules.get("close_vs_ema50_h1") == "below" and not np.isnan(ema50) and close > ema50:
            return False

        if rules.get("rsi_cross_50") and prev_row is not None:
            r0, r1 = float(prev_row.get("rsi", 50)), float(row.get("rsi", 50))
            if not (r0 < 50 <= r1 or r0 > 50 >= r1):
                return False

        if "zscore_abs_min" in rules:
            if np.isnan(z) or abs(z) < float(rules["zscore_abs_min"]):
                return False

        if rules.get("dealing_range_position") == "extreme":
            lb = int(rules.get("lookback_h1", 48))
            if len(h1_upto) < lb:
                return False
            hh = float(h1_upto["high"].tail(lb).max())
            ll = float(h1_upto["low"].tail(lb).min())
            mid = 0.5 * (hh + ll)
            rng = max(hh - ll, 1e-9)
            pos = (close - ll) / rng
            if abs(pos - 0.5) < 0.35:
                return False

        if "bb_width_max_pctile" in rules:
            lb = int(rules.get("lookback_h1", 60))
            bw = h1_upto["bbw"].dropna().tail(lb) if "bbw" in h1_upto.columns else pd.Series(dtype=float)
            if len(bw) < 10:
                return False
            thr = float(bw.quantile(float(rules["bb_width_max_pctile"])))
            cur_bw = float(row.get("bbw", np.nan))
            if np.isnan(cur_bw) or cur_bw > thr:
                return False

        if rules.get("momentum_crash_proxy"):
            if prev_row is None:
                return False
            pr = float(prev_row.get("atr_ratio", 1))
            if not (atrr > pr * 1.45 and float(row.get("ret_1", 0)) < -0.001):
                return False

        if rules.get("session_ist") == "asian_range":
            if not _session_asian_range_ist(t):
                return False
        if rules.get("session_ist") == "kill_zone":
            if not _session_kill_zone_ist(t):
                return False

        if rules.get("manipulation_proxy"):
            if not _wick_sweep_last_n(m15_upto, n=5):
                return False

        if rules.get("order_block_proxy"):
            if len(h1_upto) < 5:
                return False
            last = h1_upto.tail(3)
            spread = (last["high"].max() - last["low"].min()) / close
            if spread < 0.003:
                return False

        if rules.get("fvg_proxy"):
            if len(m15_upto) < 4:
                return False
            a, b, c = m15_upto.iloc[-3], m15_upto.iloc[-2], m15_upto.iloc[-1]
            bullish = float(a["high"]) < float(c["low"])
            bearish = float(a["low"]) > float(c["high"])
            if not (bullish or bearish):
                return False

        if rules.get("clustering_proxy"):
            if prev_row is None:
                return False
            if not (atrr > 1.15 and float(prev_row.get("atr_ratio", 0)) > 1.05):
                return False

        if rules.get("correlation_break_proxy"):
            lb = int(rules.get("lookback_h1", 20))
            r = m15_upto["close"].pct_change().tail(lb)
            if len(r) < 10:
                return False
            c0 = r.autocorr(lag=1)
            if not (c0 is not None and abs(c0) < 0.15 and float(adx) < 28):
                return False

        return True
    except Exception as exc:
        log.debug("_match_row: %s", exc)
        return False


def regime_occurrence_study(
    df_m15: pd.DataFrame,
    df_h1: pd.DataFrame,
    regime_entry: dict[str, Any],
    *,
    min_warmup: int = 100,
    step: int = 1,
    forward_bars: int = 4,
) -> dict[str, Any]:
    """
    Walk forward: fraction of bars where taxonomy rules match; optional forward M15 win rate when matched.
    """
    rid = int(regime_entry.get("id", -1))
    name = str(regime_entry.get("name", ""))
    if not regime_entry.get("detectable_from_mt5_ohlc", True):
        note = str((regime_entry.get("match_rules") or {}).get("note", "Requires external series or discretionary context"))
        return {
            "regime_id": rid,
            "name": name,
            "skipped": True,
            "reason": note,
        }
    try:
        df_h1 = _ensure_h1_bbw(df_h1)
        feat = build_aligned_features(df_m15, df_h1)
        hits = 0
        tested = 0
        fwd_wins = 0
        rules = regime_entry.get("match_rules") or {}

        for i in range(min_warmup, len(feat), step):
            row = feat.iloc[i]
            prev = feat.iloc[i - 1] if i > 0 else None
            t = row["time"]
            m15_upto = df_m15[df_m15["time"] <= t]
            h1_upto = df_h1[df_h1["time"] <= t]
            if len(h1_upto) < 30:
                continue
            tested += 1
            if not _match_row(rules, m15_upto=m15_upto, h1_upto=h1_upto, row=row, prev_row=prev):
                continue
            hits += 1
            if i + forward_bars < len(feat):
                e0 = float(row["close"])
                e1 = float(feat["close"].iloc[i + forward_bars])
                if e1 > e0:
                    fwd_wins += 1

        pct = 100.0 * hits / tested if tested else 0.0
        win_pct = 100.0 * fwd_wins / hits if hits else 0.0
        return {
            "regime_id": rid,
            "name": name,
            "skipped": False,
            "bars_tested": tested,
            "hits": hits,
            "occurrence_pct": round(pct, 3),
            "forward_win_rate_pct": round(win_pct, 3),
            "forward_bars": forward_bars,
            "practical_quadrant": regime_entry.get("practical_quadrant"),
            "suggested_strategies": regime_entry.get("suggested_strategies") or [],
        }
    except Exception as exc:
        log.exception("regime_occurrence_study")
        return {"regime_id": rid, "name": name, "skipped": True, "reason": str(exc)}


def slice_m15_window(df_m15: pd.DataFrame, df_h1: pd.DataFrame, last_n_m15: int):
    if len(df_m15) <= last_n_m15:
        return df_m15, df_h1
    tail = df_m15.tail(last_n_m15)
    t0 = tail["time"].iloc[0]
    h2 = df_h1[df_h1["time"] >= t0].copy()
    return tail.reset_index(drop=True), h2.reset_index(drop=True)
