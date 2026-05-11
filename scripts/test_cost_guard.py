"""Cost guard demo — fake `market:*` spreads, then read `cost:*` from Redis.

    python scripts/test_cost_guard.py
"""

from __future__ import annotations

from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.bus import get_value, set_value
from risk.cost_guard import SUPPORTED_SYMBOLS, evaluate_cost_guard_once


def push_market(symbol: str, bid: float, ask: float) -> None:
    """Mirror pulse key shape."""
    sym = str(symbol).upper()
    spr = abs(float(ask) - float(bid))
    set_value(f"market:{sym}:bid", bid)
    set_value(f"market:{sym}:ask", ask)
    set_value(f"market:{sym}:spread", spr)


def tight_background(except_symbol: str | None = None) -> None:
    """Keep untouched symbols in a clearly SAFE band."""
    cfg = [
        ("EURUSD", 1.08500, 1.08501),
        ("GBPUSD", 1.26500, 1.26501),
        ("USDJPY", 150.000, 150.010),
        ("XAUUSD", 2650.00, 2650.01),
        ("BTCUSD", 95000.00, 95005.00),
    ]
    for sym, b, a in cfg:
        if except_symbol and sym == except_symbol.upper():
            continue
        push_market(sym, b, a)


def print_cost_row(symbol: str) -> None:
    sym = symbol.upper()
    print(
        f"  {sym}: status={get_value(f'cost:{sym}:status')} "
        f"pips={get_value(f'cost:{sym}:spread_pips')} "
        f"block={get_value(f'cost:{sym}:block_trading')} "
        f"| {get_value(f'cost:{sym}:reason')}"
    )


def scenario(name: str, setup_fn, focus: str) -> None:
    print("")
    print("==============================================")
    print(name)
    print("==============================================")
    setup_fn()
    evaluate_cost_guard_once()
    for sym in SUPPORTED_SYMBOLS:
        print_cost_row(sym)
    print("")
    print(f"  >> focus {focus}:")
    print_cost_row(focus)


def main() -> None:
    print("QUANT_ENGINE_2026 | cost_guard | scripts/test_cost_guard.py")

    scenario(
        "1) EURUSD ~0.8 pips -> SAFE",
        lambda: (
            tight_background("EURUSD"),
            push_market("EURUSD", 1.10000, 1.10008),
        ),
        "EURUSD",
    )

    scenario(
        "2) XAUUSD ~45 pips -> BLOCKED",
        lambda: (
            tight_background("XAUUSD"),
            push_market("XAUUSD", 2650.00, 2654.50),
        ),
        "XAUUSD",
    )

    scenario(
        "3) BTCUSD ~650 pips -> BLOCKED",
        lambda: (
            tight_background("BTCUSD"),
            push_market("BTCUSD", 95000.00, 95650.00),
        ),
        "BTCUSD",
    )

    def _missing_eurusd() -> None:
        import redis

        from core.config import load_config

        tight_background("EURUSD")
        cfg = load_config()
        client = redis.Redis(
            host=cfg.get("REDIS_HOST", "localhost"),
            port=int(cfg.get("REDIS_PORT", 6379)),
            decode_responses=True,
        )
        for suffix in ("bid", "ask", "spread", "timestamp"):
            client.delete(f"market:EURUSD:{suffix}")

    scenario(
        "4) EURUSD missing market -> WARNING",
        _missing_eurusd,
        "EURUSD",
    )

    print("")
    print("Done.")


if __name__ == "__main__":
    main()
