#!/usr/bin/env python3
"""
Central runner: delegate startup order to orchestrator.RuntimeController.

Usage (from QUANT_ENGINE_2026):
    python run.py

Requires Redis. Recovery and event subscribers start before live tick ingestion.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.clock import run_clock
from core.config import describe_mt5_feed_readiness
from core.config_registry import run_config_registry
from core.heartbeat import run_heartbeat
from core import system_registry as reg
from core.pulse import run_pulse
from execution.broker_bridge import run_broker_bridge
from execution.order_tracker import run_order_tracker
from execution.trade_manager import run_trade_manager
from journal.trade_logger import run_trade_logger
from market.features.atr import run_atr_engine
from orchestrator.runtime_controller import (
    RuntimeController,
    check_foundation_or_exit,
    print_startup_summary,
)
from risk.shield import run_shield

# Worker order matters: event subscribers are started by RuntimeController first.
# Pulse starts only after tick_sanitizer and market_data_hub are subscribed.
_WORKERS: tuple[tuple[str, Callable[[], None]], ...] = (
    ("config_registry", run_config_registry),
    ("clock", run_clock),
    ("heartbeat", run_heartbeat),
    ("shield", run_shield),
    ("atr_engine", run_atr_engine),
    ("pulse", run_pulse),
    ("broker_bridge", run_broker_bridge),
    ("trade_logger", run_trade_logger),
    ("order_tracker", run_order_tracker),
    ("trade_manager", run_trade_manager),
)


def main() -> None:
    print("--------------------------------")
    print("STARTING QUANT ENGINE 2026")
    print("--------------------------------")

    # Phase: foundation — prove Redis works before recovery/worker startup.
    check_foundation_or_exit()

    feed = describe_mt5_feed_readiness()
    if not feed["ok"]:
        print("\n=== MARKET FEED CONFIGURATION WARNING ===")
        for msg in feed["issues"]:
            print(f"  ! {msg}")
        for hint in feed["hints"]:
            print(f"  · {hint}")
        print(
            "\n  Dashboard (Streamlit) only monitors — it does NOT start pulse.\n"
            "  Run `python run.py` with MetaTrader open when you want live ticks.\n"
        )
        if feed["strict_exit_requested"]:
            print("QUANT_STRICT_MT5_CONFIG is enabled — exiting before recovery.")
            sys.exit(2)

    controller = RuntimeController()
    controller.run_recovery()
    controller.start_event_subscribers()

    reg.update_phase_status("market_data", "STARTING")
    reg.update_phase_status("risk", "STARTING")

    controller.start_workers(_WORKERS)
    controller.start_registry_heartbeat_daemon()

    reg.update_phase_status("market_data", "COMPLETE")
    reg.update_phase_status("risk", "RUNNING")
    controller.mark_running_if_safe()

    print_startup_summary(controller.report)
    controller.join_workers()


if __name__ == "__main__":
    main()
