"""
core/base_system.py — Minimal runtime contract for orchestrator-started systems.

Existing modules keep their concrete implementations; this documents the shape
RuntimeController expects (`start()` / `stop()`).

Heartbeat publishing uses Redis (`system:{name}:heartbeat`) via
`core.system_registry.touch_system_heartbeat`. Streamlit must read those keys —
do not rely on `_running` inside the dashboard process.
"""

from __future__ import annotations

from typing import Protocol


class RuntimeSystem(Protocol):
    """In-process singletons started via orchestrator lazy-load."""

    def start(self) -> None:
        """Subscribe to EventBus / register callbacks."""

    def stop(self) -> None:
        """Unsubscribe / release resources (best effort)."""


def touch_registry_heartbeat(system_name: str) -> None:
    """Thin wrapper so components avoid repeating registry imports."""
    from core import system_registry as reg

    reg.touch_system_heartbeat(system_name)
