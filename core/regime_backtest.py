"""
Walk-forward regime labels on historical M15/H1 (no orders).

Use MT5 history via ``data_feed.get_candles`` / ``get_h1_candles``, or pass CSV-derived
DataFrames. Each step calls ``detect_regime`` with the bar's close time so the session
voter matches history (not wall-clock now).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.regime_detector import detect_regime

log = logging.getLogger(__name__)

def _ensure_utc(ts: Any) -> datetime:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t.to_pydatetime()


def run_regime_walk_forward(
    df_m15: pd.DataFrame,
    df_h1: pd.DataFrame,
    *,
    min_m15_bars: int = 100,
    step: int = 1,
    output_csv: Path | None = None,
) -> pd.DataFrame:
    """
    For each M15 index ``i >= min_m15_bars``, slice data as-of that bar's ``time``,
    run ``detect_regime`` with ``reference_time_utc`` set to that bar close.

    Returns a DataFrame with columns: time, label, confidence, transition_warning,
    duration_candles, adx_h1, atr_ratio, rsi_m15, voter_indicator, voter_structure, voter_session.
    """
    try:
        rows: list[dict[str, Any]] = []
        mem: dict[str, Any] = {}
        n = len(df_m15)
        if n < min_m15_bars + 1:
            log.warning("M15 series too short for walk-forward")
            return pd.DataFrame()

        h1 = df_h1.copy()
        m15 = df_m15.copy()
        for col in ("time",):
            if col not in m15.columns or col not in h1.columns:
                raise ValueError("DataFrames must have 'time' column")

        for i in range(min_m15_bars, n, step):
            t_raw = m15["time"].iloc[i]
            t_utc = _ensure_utc(t_raw)
            m15_sub = m15.iloc[: i + 1].copy()
            h1_sub = h1[h1["time"] <= m15["time"].iloc[i]].copy()
            if len(h1_sub) < 60:
                continue
            try:
                r = detect_regime(
                    m15_sub,
                    h1_sub,
                    reference_time_utc=t_utc,
                    persist_regime_history=False,
                    regime_memory=mem,
                )
            except Exception as exc:
                log.debug("regime step %s: %s", i, exc)
                continue
            met = r.get("metrics") or {}
            vot = r.get("voters") or {}
            rows.append(
                {
                    "time": t_utc,
                    "label": r.get("label"),
                    "confidence": r.get("confidence"),
                    "transition_warning": r.get("transition_warning"),
                    "duration_candles": r.get("duration_candles"),
                    "adx_h1": met.get("adx_h1"),
                    "atr_ratio": met.get("atr_ratio"),
                    "rsi_m15": met.get("rsi_m15"),
                    "voter_indicator": vot.get("indicator"),
                    "voter_structure": vot.get("structure"),
                    "voter_session": vot.get("session"),
                }
            )

        out = pd.DataFrame(rows)
        if output_csv is not None and len(out):
            output_csv.parent.mkdir(parents=True, exist_ok=True)
            out.to_csv(output_csv, index=False)
            log.info("Wrote regime walk-forward CSV: %s", output_csv)
        return out
    except Exception as exc:
        log.exception("run_regime_walk_forward failed: %s", exc)
        raise RuntimeError(f"run_regime_walk_forward failed: {exc}") from exc


def summarize_regime_series(df: pd.DataFrame) -> dict[str, Any]:
    """Return label counts and fraction SKIP / Q4 for quick inspection."""
    try:
        if df is None or len(df) == 0:
            return {"bars": 0}
        vc = df["label"].value_counts().to_dict()
        n = len(df)
        return {
            "bars": n,
            "label_counts": vc,
            "pct_skip": round(100.0 * vc.get("SKIP", 0) / n, 2),
            "last_label": df["label"].iloc[-1],
            "last_confidence": float(df["confidence"].iloc[-1]) if "confidence" in df.columns else None,
        }
    except Exception as exc:
        log.warning("summarize_regime_series: %s", exc)
        return {"error": str(exc)}
