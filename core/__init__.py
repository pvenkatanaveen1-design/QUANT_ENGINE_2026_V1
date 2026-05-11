"""
core package — lazy exports only.

Importing submodules like ``core.data_feed`` or ``core.regime_detector`` must **not**
pull in the event bus or state store. Legacy code may still use
``from core import bus`` / ``state`` / ``config``; those names load on first access.
"""

from __future__ import annotations

from typing import Any

__all__ = ["bus", "state", "config"]


def __getattr__(name: str) -> Any:
    if name == "bus":
        from core.event_bus import bus as _bus

        return _bus
    if name == "state":
        from core.state_store import state as _state

        return _state
    if name == "config":
        import core.config_manager as _config

        return _config
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
