"""
Strategy allowlist from config/strategies.yaml. No Redis.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
_STRATEGIES_PATH = _ENGINE_ROOT / "config" / "strategies.yaml"
_CACHE: dict[str, Any] | None = None


def _load_yaml() -> dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        if not _STRATEGIES_PATH.exists():
            log.warning("config/strategies.yaml missing — no strategies")
            _CACHE = {"strategies": {}}
            return _CACHE
        with open(_STRATEGIES_PATH, encoding="utf-8") as f:
            _CACHE = yaml.safe_load(f) or {}
        return _CACHE
    except Exception as exc:
        log.warning("strategies yaml load failed: %s", exc)
        _CACHE = {"strategies": {}}
        return _CACHE


def reload_strategies() -> None:
    global _CACHE
    _CACHE = None


def get_strategies(regime_label: str) -> list[str]:
    """Return enabled strategy names allowed for regime (Q1..Q4 or SKIP)."""
    try:
        lab = str(regime_label).strip().upper()
        if lab == "SKIP":
            return []
        data = _load_yaml()
        block = data.get("strategies") or {}
        out: list[str] = []
        for name, cfg in block.items():
            if not isinstance(cfg, dict):
                continue
            if not cfg.get("enabled", True):
                continue
            regimes = cfg.get("regimes") or []
            if lab in [str(r).upper() for r in regimes]:
                out.append(str(name))
        return out
    except Exception as exc:
        log.error("get_strategies failed: %s", exc)
        return []


def get_strategy_params(strategy_name: str) -> dict[str, Any]:
    try:
        data = _load_yaml()
        block = data.get("strategies") or {}
        cfg = block.get(strategy_name)
        if isinstance(cfg, dict):
            return dict(cfg)
        return {}
    except Exception as exc:
        log.error("get_strategy_params failed: %s", exc)
        return {}
