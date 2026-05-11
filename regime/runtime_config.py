from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any

import yaml

ENGINE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ENGINE_ROOT / "config" / "regime"
SNAPSHOT_DIR = CONFIG_DIR / "snapshots"
AUDIT_PATH = ENGINE_ROOT / "logs" / "regime_config_audit.jsonl"

FILE_SECTIONS: dict[str, tuple[str, ...]] = {
    "adx.yaml": ("adx",),
    "atr.yaml": ("atr",),
    "structure.yaml": ("structure", "session"),
    "probability.yaml": ("probability", "validator"),
    "strategy_mapping.yaml": ("strategy_mapping",),
}

REGIME_PARAM_SPECS: dict[str, dict[str, dict[str, Any]]] = {
    "adx": {
        "weak_trend_threshold": {"default": 25.0, "min": 10.0, "max": 60.0, "recommended": [20.0, 35.0], "warning": [15.0, 45.0]},
        "strong_trend_threshold": {"default": 30.0, "min": 10.0, "max": 70.0, "recommended": [25.0, 45.0], "warning": [20.0, 55.0]},
        "smoothing_period": {"default": 14, "min": 5, "max": 60, "recommended": [10, 20], "warning": [7, 35]},
    },
    "atr": {
        "low_vol_percentile": {"default": 25.0, "min": 1.0, "max": 60.0, "recommended": [15.0, 35.0], "warning": [10.0, 45.0]},
        "high_vol_percentile": {"default": 80.0, "min": 50.0, "max": 99.0, "recommended": [70.0, 90.0], "warning": [60.0, 95.0]},
        "chaotic_vol_percentile": {"default": 95.0, "min": 70.0, "max": 99.9, "recommended": [90.0, 98.0], "warning": [85.0, 99.0]},
        "expansion_change_pct": {"default": 0.05, "min": 0.0, "max": 1.0, "recommended": [0.02, 0.12], "warning": [0.01, 0.2]},
    },
    "rsi": {
        "overbought": {"default": 70.0, "min": 50.0, "max": 95.0, "recommended": [65.0, 80.0], "warning": [60.0, 90.0]},
        "oversold": {"default": 30.0, "min": 5.0, "max": 50.0, "recommended": [20.0, 35.0], "warning": [10.0, 40.0]},
        "neutral_low": {"default": 45.0, "min": 10.0, "max": 60.0, "recommended": [40.0, 48.0], "warning": [35.0, 55.0]},
        "neutral_high": {"default": 55.0, "min": 40.0, "max": 90.0, "recommended": [52.0, 60.0], "warning": [48.0, 65.0]},
    },
    "volume": {
        "spike_multiplier": {"default": 1.8, "min": 1.0, "max": 6.0, "recommended": [1.4, 2.5], "warning": [1.2, 3.5]},
        "dry_multiplier": {"default": 0.6, "min": 0.1, "max": 1.0, "recommended": [0.4, 0.8], "warning": [0.2, 0.9]},
    },
    "structure": {
        "breakout_sensitivity": {"default": 1.0, "min": 0.1, "max": 3.0, "recommended": [0.8, 1.5], "warning": [0.5, 2.0]},
        "consolidation_length": {"default": 20, "min": 5, "max": 200, "recommended": [12, 50], "warning": [8, 100]},
        "swing_lookback": {"default": 6, "min": 3, "max": 50, "recommended": [5, 14], "warning": [4, 25]},
    },
    "session": {
        "asia_start_hour_ist": {"default": 5.5, "min": 0.0, "max": 23.9, "recommended": [4.0, 8.0], "warning": [2.0, 10.0]},
        "london_start_hour_ist": {"default": 12.5, "min": 0.0, "max": 23.9, "recommended": [11.5, 14.0], "warning": [10.0, 16.0]},
        "newyork_start_hour_ist": {"default": 18.5, "min": 0.0, "max": 23.9, "recommended": [17.0, 20.0], "warning": [15.0, 22.0]},
        "dst_offset_hours": {"default": 0.0, "min": -2.0, "max": 2.0, "recommended": [-1.0, 1.0], "warning": [-2.0, 2.0]},
    },
    "validator": {
        "min_persistence_candles": {"default": 3, "min": 1, "max": 100, "recommended": [2, 6], "warning": [1, 12]},
        "confidence_threshold": {"default": 0.70, "min": 0.01, "max": 0.99, "recommended": [0.6, 0.85], "warning": [0.5, 0.9]},
        "regime_flip_cooldown": {"default": 1, "min": 0, "max": 20, "recommended": [0, 4], "warning": [0, 8]},
    },
    "probability": {
        "probability_threshold": {"default": 0.55, "min": 0.0, "max": 1.0, "recommended": [0.5, 0.75], "warning": [0.4, 0.85]},
        "uncertainty_threshold": {"default": 0.50, "min": 0.0, "max": 1.0, "recommended": [0.2, 0.6], "warning": [0.1, 0.8]},
        "weight_adx": {"default": 1.0, "min": 0.0, "max": 5.0, "recommended": [0.5, 2.0], "warning": [0.1, 3.0]},
        "weight_atr": {"default": 1.0, "min": 0.0, "max": 5.0, "recommended": [0.5, 2.0], "warning": [0.1, 3.0]},
        "weight_structure": {"default": 1.0, "min": 0.0, "max": 5.0, "recommended": [0.5, 2.5], "warning": [0.1, 3.5]},
        "weight_session": {"default": 1.0, "min": 0.0, "max": 5.0, "recommended": [0.2, 2.0], "warning": [0.0, 3.0]},
        "weight_volume": {"default": 1.0, "min": 0.0, "max": 5.0, "recommended": [0.2, 2.0], "warning": [0.0, 3.0]},
        "weight_momentum": {"default": 1.0, "min": 0.0, "max": 5.0, "recommended": [0.2, 2.0], "warning": [0.0, 3.0]},
    },
    "strategy_mapping": {
        "trend_size_multiplier": {"default": 1.0, "min": 0.0, "max": 2.0, "recommended": [0.5, 1.2], "warning": [0.25, 1.5]},
        "range_size_multiplier": {"default": 0.8, "min": 0.0, "max": 2.0, "recommended": [0.4, 1.0], "warning": [0.2, 1.2]},
        "high_vol_size_multiplier": {"default": 0.6, "min": 0.0, "max": 2.0, "recommended": [0.2, 0.8], "warning": [0.0, 1.0]},
        "chaos_size_multiplier": {"default": 0.0, "min": 0.0, "max": 1.0, "recommended": [0.0, 0.1], "warning": [0.0, 0.2]},
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _defaults() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for section, params in REGIME_PARAM_SPECS.items():
        out[section] = {name: meta["default"] for name, meta in params.items()}
    return out


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)


def ensure_regime_config_files() -> None:
    defaults = _defaults()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for filename, sections in FILE_SECTIONS.items():
        p = CONFIG_DIR / filename
        if p.exists():
            continue
        data = {sec: defaults.get(sec, {}) for sec in sections}
        data["_meta"] = {"created_at": _utc_now(), "source": "default"}
        _write_yaml(p, data)


def load_regime_runtime_config() -> dict[str, dict[str, Any]]:
    ensure_regime_config_files()
    cfg = _defaults()
    for filename in FILE_SECTIONS:
        p = CONFIG_DIR / filename
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = yaml.safe_load(f) or {}
            if not isinstance(d, dict):
                continue
            for section in cfg:
                if isinstance(d.get(section), dict):
                    cfg[section].update(d[section])
        except Exception:
            continue
    return cfg


def _validate_one(section: str, name: str, value: Any) -> tuple[bool, str]:
    meta = REGIME_PARAM_SPECS.get(section, {}).get(name)
    if not meta:
        return False, f"unknown_param:{section}.{name}"
    lo, hi = meta["min"], meta["max"]
    try:
        numeric = float(value)
    except Exception:
        return False, f"not_numeric:{section}.{name}"
    if numeric < float(lo) or numeric > float(hi):
        return False, f"out_of_range:{section}.{name} {numeric} not in [{lo},{hi}]"
    return True, ""


def validate_runtime_config(config_values: dict[str, dict[str, Any]]) -> list[str]:
    errs: list[str] = []
    for section, params in REGIME_PARAM_SPECS.items():
        values = config_values.get(section, {})
        for name in params:
            ok, msg = _validate_one(section, name, values.get(name, params[name]["default"]))
            if not ok:
                errs.append(msg)
    # Cross-validation
    if config_values["adx"]["strong_trend_threshold"] <= config_values["adx"]["weak_trend_threshold"]:
        errs.append("adx.strong_trend_threshold must be > adx.weak_trend_threshold")
    if config_values["rsi"]["oversold"] >= config_values["rsi"]["overbought"]:
        errs.append("rsi.oversold must be < rsi.overbought")
    return errs


def save_runtime_config(config_values: dict[str, dict[str, Any]], source: str = "ui") -> None:
    ensure_regime_config_files()
    errors = validate_runtime_config(config_values)
    if errors:
        raise ValueError("; ".join(errors))
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, sections in FILE_SECTIONS.items():
        payload = {sec: config_values.get(sec, {}) for sec in sections}
        payload["_meta"] = {"updated_at": _utc_now(), "source": source}
        _write_yaml(CONFIG_DIR / filename, payload)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _write_yaml(SNAPSHOT_DIR / f"{stamp}.yaml", config_values)


def audit_change(old_values: dict[str, dict[str, Any]], new_values: dict[str, dict[str, Any]], source: str = "ui") -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    changes: list[dict[str, Any]] = []
    for section, params in REGIME_PARAM_SPECS.items():
        for name in params:
            old = old_values.get(section, {}).get(name)
            new = new_values.get(section, {}).get(name)
            if old != new:
                changes.append({"key": f"{section}.{name}", "old": old, "new": new})
    if not changes:
        return
    row = {"ts": _utc_now(), "source": source, "changes": changes}
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def reset_defaults() -> dict[str, dict[str, Any]]:
    return deepcopy(_defaults())

