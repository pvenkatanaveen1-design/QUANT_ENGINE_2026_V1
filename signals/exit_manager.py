from __future__ import annotations

from typing import Any


def default_exit_meta(_signal: dict[str, Any]) -> dict[str, Any]:
    """Phase 0 exit policy hook — expand with partials/trailing later."""
    return {"mode": "fixed_rr"}
