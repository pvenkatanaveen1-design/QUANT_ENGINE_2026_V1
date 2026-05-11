"""Load ``config/regime_taxonomy.yaml`` — 52-regime catalog and quadrant mapping."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _ROOT / "config" / "regime_taxonomy.yaml"
_CACHE: dict[str, Any] | None = None


def load_taxonomy(path: Path | None = None) -> dict[str, Any]:
    global _CACHE
    p = path or _DEFAULT_PATH
    try:
        if _CACHE is not None and path is None:
            return _CACHE
        if not p.exists():
            log.warning("regime_taxonomy.yaml missing at %s", p)
            return {}
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if path is None:
            _CACHE = data
        return data
    except Exception as exc:
        log.exception("load_taxonomy: %s", exc)
        return {}


def get_regime(data: dict[str, Any], regime_id: int) -> dict[str, Any] | None:
    for r in data.get("regimes") or []:
        if int(r.get("id", -1)) == int(regime_id):
            return r
    return None


def list_regime_options(data: dict[str, Any]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for r in sorted(data.get("regimes") or [], key=lambda x: int(x.get("id", 0))):
        rid = int(r["id"])
        name = str(r.get("name", ""))
        sec = str(r.get("section_title", ""))[:40]
        out.append((rid, f"[{rid}] {name} — {sec}"))
    return out
