from __future__ import annotations

from datetime import datetime

from core.clock import utc_now
from regime.classifiers.adx_regime import adx_vote
from regime.classifiers.atr_regime import atr_vote
from regime.classifiers.structure_regime import structure_vote
from schemas.regime import RegimeLabel, RegimeState


class EnsembleRegimeClassifier:
    def classify(
        self,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        time_utc: datetime | None = None,
    ) -> RegimeState:
        t = utc_now() if time_utc is None else time_utc
        v_adx = adx_vote(highs, lows, closes)
        v_atr = atr_vote(highs, lows, closes)
        v_struct = structure_vote(closes)

        components: dict[str, float] = {}
        votes: list[float] = []
        if v_adx is not None:
            components["adx"] = v_adx
            votes.append(v_adx)
        if v_atr is not None:
            components["atr"] = v_atr
            votes.append(v_atr * 0.5)
        if v_struct is not None:
            components["structure"] = v_struct
            votes.append(v_struct)

        if not votes:
            return RegimeState(
                label=RegimeLabel.UNKNOWN,
                confidence=0.0,
                components=components,
                time_utc=t,
            )

        score = sum(votes) / max(1, len(votes))
        conf = min(1.0, abs(score))

        if abs(score) < 0.15:
            label = RegimeLabel.TRANSITION
        elif abs(score) < 0.35:
            label = RegimeLabel.RANGE
        else:
            label = RegimeLabel.TREND

        return RegimeState(label=label, confidence=conf, components=components, time_utc=t)
