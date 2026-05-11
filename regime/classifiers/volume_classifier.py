from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VolumeClassification:
    volume_signal: str
    volume_ratio: float


def classify_volume(
    volumes: list[float],
    *,
    spike_multiplier: float = 1.8,
    dry_multiplier: float = 0.6,
) -> VolumeClassification:
    if len(volumes) < 21:
        return VolumeClassification("NORMAL", 1.0)
    avg = sum(volumes[-21:-1]) / 20.0
    cur = volumes[-1]
    if avg <= 0:
        return VolumeClassification("NORMAL", 1.0)
    ratio = cur / avg
    if ratio >= spike_multiplier:
        sig = "SPIKE"
    elif ratio <= dry_multiplier:
        sig = "DRY"
    else:
        sig = "NORMAL"
    return VolumeClassification(sig, ratio)

