"""
core/__init__.py — Makes `core` a Python package and exports the three singletons.

Usage anywhere in the engine:
    from core import bus, state, config

    bus.publish(EventType.KILL_SWITCH, {"reason": "DD"}, source="shield")
    equity = state.get_equity()
    limits = config.load("risk_rules")
"""

# The in-process event bus for system decoupling
from core.event_bus   import bus

# The live account state store (equity, DD, kill switch, open trades)
from core.state_store import state

# The YAML + .env config loader
import core.config_manager as config

__all__ = ["bus", "state", "config"]
