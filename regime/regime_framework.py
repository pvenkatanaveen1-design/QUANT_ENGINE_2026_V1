from __future__ import annotations

# Research/analytics taxonomy (52 labels) grouped into 10 categories.
# Live runtime still maps to 12 operational states for safety/performance.

REGIME_CATEGORIES: dict[str, list[str]] = {
    "Trend": ["TREND_PERSISTENT", "TREND_ACCELERATING", "TREND_DECELERATING", "TREND_PULLBACK", "TREND_EXHAUSTION"],
    "Mean Reversion": ["MR_BALANCED", "MR_OVERSHOOT", "MR_VWAP_REVERT", "MR_SESSION_REVERT", "MR_LIQUIDITY_REVERT"],
    "Wyckoff": ["WY_ACCUMULATION", "WY_MARKUP", "WY_DISTRIBUTION", "WY_MARKDOWN", "WY_SPRING", "WY_UPTHRUST"],
    "Macro/Central Bank": ["MACRO_HAWKISH", "MACRO_DOVISH", "MACRO_REAL_YIELD_UP", "MACRO_REAL_YIELD_DOWN", "MACRO_POLICY_WAIT"],
    "Sentiment/Risk": ["RISK_ON", "RISK_OFF", "SENTIMENT_SQUEEZE", "SENTIMENT_PANIC", "SENTIMENT_RECOVERY"],
    "News/Event": ["NEWS_PRE", "NEWS_SPIKE", "NEWS_AFTERSHOCK", "NEWS_DIGESTION", "EVENT_DRIFT"],
    "ICT/Order Flow": ["ICT_SWEEP_BUY", "ICT_SWEEP_SELL", "ICT_FVG_EXPAND", "ICT_FVG_FILL", "ICT_OB_REACTION"],
    "Microstructure": ["MICRO_COMPRESSION", "MICRO_EXPANSION", "MICRO_SPREAD_WIDE", "MICRO_SPREAD_TIGHT", "MICRO_LATENCY_RISK"],
    "Quantitative": ["Q_LOW_VAR", "Q_HIGH_VAR", "Q_HMM_STATE_1", "Q_HMM_STATE_2", "Q_HMM_STATE_3", "Q_CORR_BREAK"],
    "Intermarket": ["IM_DXY_UP_GOLD_DOWN", "IM_DXY_DOWN_GOLD_UP", "IM_YIELD_UP_PRESSURE", "IM_YIELD_DOWN_RELIEF", "IM_CRUDE_LINK"],
}

ALL_52_REGIMES: list[str] = [label for labels in REGIME_CATEGORIES.values() for label in labels]

# Ensure exactly 52 research labels.
if len(ALL_52_REGIMES) != 52:
    raise RuntimeError(f"Expected 52 research regimes, found {len(ALL_52_REGIMES)}")

OPERATING_QUADRANTS: dict[str, list[str]] = {
    "Q1_TREND_FOLLOW": ["TREND_HIGH_VOL", "BREAKOUT_EXPANSION", "PULLBACK_CONTINUATION"],
    "Q2_MEAN_REVERT": ["RANGE_LOW_VOL", "RANGE_HIGH_VOL", "LIQUIDITY_SWEEP", "NY_REVERSAL"],
    "Q3_WAIT_FILTER": ["ASIA_COMPRESSION", "TRANSITION", "EXHAUSTION"],
    "Q4_HARD_BLOCK": ["NEWS_CHAOS"],
}

