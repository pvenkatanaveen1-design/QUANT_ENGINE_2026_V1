"""
Map canonical regime labels to strategy playbooks and score candidates.
"""

from __future__ import annotations

from typing import ClassVar


class RegimeStrategyRouter:
    registry: ClassVar[dict[str, list[str]]] = {
        "TRENDING_UP": ["breakout_momentum", "asian_range_sweep"],
        "TRENDING_DOWN": ["breakout_momentum", "asian_range_sweep"],
        "RANGING": ["mean_reversion", "pdh_pdl_fade"],
        "VOLATILE": ["volatility_contraction", "news_fade"],
        "UNKNOWN": ["conservative_breakout"],
    }

    # Ensemble / external labels -> canonical keys in ``registry``.
    _EXTERNAL_TO_CANONICAL: ClassVar[dict[str, str]] = {
        "Q1_TREND": "TRENDING_UP",
        "Q2_BREAKOUT": "TRENDING_DOWN",
        "Q3_RANGE": "RANGING",
        "Q4_STRESS": "VOLATILE",
    }

    @classmethod
    def _normalize_regime_label(cls, regime_label: str) -> str:
        raw = str(regime_label).strip().upper()
        if raw in cls.registry:
            return raw
        mapped = cls._EXTERNAL_TO_CANONICAL.get(raw)
        if mapped is not None:
            return mapped
        return "UNKNOWN"

    @classmethod
    def get_strategies(cls, regime_label: str) -> list[str]:
        key = cls._normalize_regime_label(regime_label)
        return list(cls.registry.get(key, cls.registry["UNKNOWN"]))

    @classmethod
    def score_strategy(
        cls,
        strategy_name: str,
        regime_label: str,
        atr: float,
        spread: float,
    ) -> float:
        allowed = cls.get_strategies(regime_label)
        base = 60.0 if strategy_name in allowed else 0.0
        spread_penalty = min(20.0, max(0.0, float(spread) * 2.0))
        atr_bonus = 0.0
        if atr > 0.0005:
            atr_bonus = min(20.0, (float(atr) - 0.0005) * 40_000.0)
        score = base - spread_penalty + atr_bonus
        return max(0.0, min(100.0, round(score, 2)))

    @classmethod
    def best_strategy(
        cls,
        regime_label: str,
        atr: float,
        spread: float,
    ) -> tuple[str, float]:
        names = cls.get_strategies(regime_label)
        ranked = [
            (cls.score_strategy(name, regime_label, atr, spread), -i, name)
            for i, name in enumerate(names)
        ]
        _s, _neg_i, winner = max(ranked)
        winner_score = cls.score_strategy(winner, regime_label, atr, spread)
        return winner, winner_score
