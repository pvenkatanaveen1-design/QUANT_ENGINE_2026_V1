"""SYSTEM_MODE from `.env` — isolated from `config.py` for safe import order."""

from __future__ import annotations

import os
from enum import Enum
from typing import Final

from dotenv import load_dotenv

load_dotenv()


class SystemMode(str, Enum):
    """
    Allowed deployment modes (strings match `.env` / Redis / dashboard).

    Use `str` mixin so JSON and comparisons like `mode == "TEST"` stay natural.
    """

    TEST = "TEST"
    LIVE = "LIVE"


def _parse_system_mode() -> SystemMode:
    """Normalize env; unknown or blank → TEST (same rule as Phase 5.1)."""
    raw = os.getenv("SYSTEM_MODE", "TEST")
    token = str(raw).strip().upper()
    if token == SystemMode.LIVE.value:
        return SystemMode.LIVE
    return SystemMode.TEST


# Resolved once at import — restart process after changing `.env`.
SYSTEM_MODE: Final[SystemMode] = _parse_system_mode()


def get_system_mode() -> str:
    """Return `"TEST"` or `"LIVE"` for backward-compatible callers."""
    return SYSTEM_MODE.value


def get_system_mode_enum() -> SystemMode:
    """Typed variant when you prefer Enum over raw strings."""
    return SYSTEM_MODE


def is_live() -> bool:
    return SYSTEM_MODE is SystemMode.LIVE


def is_test_mode() -> bool:
    """True when not LIVE (includes invalid env → defaulted to TEST)."""
    return SYSTEM_MODE is SystemMode.TEST


# Informal alias if you prefer `is_testing()` naming.
is_testing = is_test_mode
