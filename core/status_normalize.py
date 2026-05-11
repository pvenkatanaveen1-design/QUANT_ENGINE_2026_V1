"""Map Redis / JSON values to canonical status tokens shared by heartbeat and dashboard."""

from typing import Any, Literal

# Which output family to use. Booleans only map correctly when `aspect` matches the key meaning.
Aspect = Literal["health", "freshness", "lifecycle", "connection"]

# Rollup written to heartbeat:overall (string).
OverallLevel = Literal["HEALTHY", "DEGRADED", "UNHEALTHY"]


def _aspect_default(aspect: Aspect) -> str:
    """Pessimistic defaults when value is missing or cannot be parsed."""
    if aspect == "health":
        return "unhealthy"
    if aspect == "freshness":
        return "stale"
    if aspect == "connection":
        return "disconnected"
    return "stopped"


def _unwrap_dict(value: Any, aspect: Aspect) -> Any:
    """
    Pull a scalar out of nested dict payloads.

    MT5 often stores {'connected': true, 'status': 'connected'} — connection aspect
    prefers the explicit connected flag when present.
    """
    if not isinstance(value, dict):
        return value

    if aspect == "connection" and "connected" in value:
        return value.get("connected")

    if aspect == "health" and "connected" in value:
        return value.get("connected")

    for key in ("status", "state", "value", "text"):
        if key in value:
            return value.get(key)

    return value


def normalize_status(value: Any, *, aspect: Aspect = "health") -> str:
    """
    Normalize Redis-friendly values to one canonical token.

    Positional-only meaning in practice: pass `value`; always set `aspect` when the
    value is a boolean (same True/False maps to different tokens per aspect).

    Standard outputs by aspect:
      - health      -> healthy | unhealthy
      - freshness   -> fresh | stale
      - lifecycle   -> running | stopped
      - connection  -> connected | disconnected

    Strings are lowercased and stripped; unknown strings fall back to the pessimistic default.
    """
    value = _unwrap_dict(value, aspect)

    if value is None:
        return _aspect_default(aspect)

    if isinstance(value, bool):
        if aspect == "health":
            return "healthy" if value else "unhealthy"
        if aspect == "freshness":
            return "fresh" if value else "stale"
        if aspect == "connection":
            return "connected" if value else "disconnected"
        return "running" if value else "stopped"

    if isinstance(value, (int, float)):
        if value == 1:
            return normalize_status(True, aspect=aspect)
        if value == 0:
            return normalize_status(False, aspect=aspect)
        return _aspect_default(aspect)

    text = str(value).strip().lower()
    if not text:
        return _aspect_default(aspect)

    if aspect == "health":
        if text in {
            "healthy",
            "ok",
            "up",
            "online",
            "active",
            "live",
            "true",
            "1",
            "yes",
            "running",
            "connected",
            "connect",
        }:
            return "healthy"
        if text in {
            "unhealthy",
            "down",
            "offline",
            "disconnected",
            "inactive",
            "false",
            "0",
            "no",
            "error",
            "stopped",
            "stale",
        }:
            return "unhealthy"
        if "fail" in text or "error" in text:
            return "unhealthy"
        if text == "starting":
            return "healthy"
        return "unhealthy"

    if aspect == "freshness":
        if text in {"fresh", "ok", "good", "true", "1", "yes", "running"}:
            return "fresh"
        if text in {"stale", "old", "bad", "false", "0", "no", "error", "stopped"}:
            return "stale"
        if "stale" in text or "fail" in text or "error" in text:
            return "stale"
        return "fresh" if text == "starting" else "stale"

    if aspect == "connection":
        if text in {"connected", "connect", "ok", "up", "online", "true", "1", "yes", "active", "live"}:
            return "connected"
        if text in {"disconnected", "down", "offline", "inactive", "false", "0", "no", "error", "stopped"}:
            return "disconnected"
        if "fail" in text or "error" in text:
            return "disconnected"
        return "disconnected"

    # lifecycle
    if text in {"running", "starting", "started", "ok", "active", "live", "online", "healthy", "true", "1", "yes"}:
        return "running"
    if text in {"stopped", "stop", "stopping", "inactive", "offline", "down", "false", "0", "no", "error"}:
        return "stopped"
    if "fail" in text or "error" in text:
        return "stopped"
    return "stopped"


def normalize_overall_level(value: Any) -> OverallLevel:
    """
    Parse heartbeat:overall from Redis (string tri-state or legacy boolean).

    Missing values are treated as UNHEALTHY (critical gap).
    """
    if value is None:
        return "UNHEALTHY"
    if isinstance(value, bool):
        return "HEALTHY" if value else "UNHEALTHY"
    text = str(value).strip().upper()
    if text == "HEALTHY":
        return "HEALTHY"
    if text == "DEGRADED":
        return "DEGRADED"
    if text == "UNHEALTHY":
        return "UNHEALTHY"
    return "UNHEALTHY"


def rollup_overall(severities: list[str]) -> OverallLevel:
    """
    Combine subsystem severities into heartbeat:overall.

    Rules:
      - any 'critical' -> UNHEALTHY
      - else any 'warn' -> DEGRADED
      - else HEALTHY
    """
    if "critical" in severities:
        return "UNHEALTHY"
    if "warn" in severities:
        return "DEGRADED"
    return "HEALTHY"
