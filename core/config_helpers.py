"""Backward-compatible alias — prefer `core.config.get_all_config`."""

from __future__ import annotations


def build_all_config() -> dict:
    """Same as `get_all_config()`; kept for older call sites."""
    from core.config import get_all_config

    return get_all_config()
