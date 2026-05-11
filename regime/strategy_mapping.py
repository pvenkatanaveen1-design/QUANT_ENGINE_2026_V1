from __future__ import annotations


def map_regime_to_strategy(regime_label: str, *, multipliers: dict[str, float] | None = None) -> dict[str, object]:
    m = multipliers or {}
    trend_mult = float(m.get("trend_size_multiplier", 1.0))
    range_mult = float(m.get("range_size_multiplier", 0.8))
    high_vol_mult = float(m.get("high_vol_size_multiplier", 0.6))
    chaos_mult = float(m.get("chaos_size_multiplier", 0.0))
    rules = {
        "NEWS_CHAOS": {"enabled": [], "blocked": ["alpha_breakout", "alpha_pullback", "alpha_sweep"], "risk_mode": "NO_TRADE", "size_mult": chaos_mult},
        "TRANSITION": {"enabled": [], "blocked": ["alpha_breakout", "alpha_pullback", "alpha_sweep"], "risk_mode": "REDUCE", "size_mult": 0.25},
        "TREND_LOW_VOL": {"enabled": ["alpha_pullback"], "blocked": ["alpha_sweep"], "risk_mode": "NORMAL", "size_mult": trend_mult},
        "TREND_HIGH_VOL": {"enabled": ["alpha_breakout", "alpha_pullback"], "blocked": ["alpha_sweep"], "risk_mode": "CAUTIOUS", "size_mult": high_vol_mult},
        "BREAKOUT_EXPANSION": {"enabled": ["alpha_breakout"], "blocked": ["alpha_sweep"], "risk_mode": "NORMAL", "size_mult": trend_mult},
        "RANGE_LOW_VOL": {"enabled": ["alpha_sweep"], "blocked": ["alpha_breakout"], "risk_mode": "NORMAL", "size_mult": range_mult},
        "RANGE_HIGH_VOL": {"enabled": ["alpha_sweep"], "blocked": ["alpha_breakout", "alpha_pullback"], "risk_mode": "CAUTIOUS", "size_mult": high_vol_mult},
        "ASIA_COMPRESSION": {"enabled": [], "blocked": ["alpha_breakout", "alpha_pullback"], "risk_mode": "NO_TRADE", "size_mult": 0.0},
        "NY_REVERSAL": {"enabled": ["alpha_sweep"], "blocked": ["alpha_breakout"], "risk_mode": "CAUTIOUS", "size_mult": high_vol_mult},
        "LIQUIDITY_SWEEP": {"enabled": ["alpha_sweep"], "blocked": ["alpha_breakout"], "risk_mode": "NORMAL", "size_mult": range_mult},
        "PULLBACK_CONTINUATION": {"enabled": ["alpha_pullback"], "blocked": ["alpha_sweep"], "risk_mode": "NORMAL", "size_mult": trend_mult},
        "EXHAUSTION": {"enabled": [], "blocked": ["alpha_breakout", "alpha_pullback", "alpha_sweep"], "risk_mode": "NO_TRADE", "size_mult": 0.0},
    }
    return rules.get(regime_label, {"enabled": [], "blocked": [], "risk_mode": "REDUCE", "size_mult": 0.5})

