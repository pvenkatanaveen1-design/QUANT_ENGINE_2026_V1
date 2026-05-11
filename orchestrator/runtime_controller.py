"""
orchestrator/runtime_controller.py — single owner for runtime startup order.

This layer intentionally owns control flow only:
- recovery before live workers
- event subscribers before tick ingestion
- worker lifecycle registration
- fail-safe startup blocking

Business logic stays in systems/, risk/, execution/, and repositories/.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core import system_registry as reg
from core.logger import LogCategory, get_logger
from core.recovery_manager import RecoveryManager, RecoveryReport
from core.state_store import state

log = get_logger("runtime_controller", LogCategory.SYSTEM)


@dataclass
class StartupReport:
    """Structured startup result for dashboard and operator review."""

    recovery: RecoveryReport | None = None
    started_subscribers: list[str] = field(default_factory=list)
    failed_subscribers: dict[str, str] = field(default_factory=dict)
    started_workers: list[str] = field(default_factory=list)
    failed_workers: dict[str, str] = field(default_factory=dict)
    safe_to_trade: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovery": self.recovery.to_dict() if self.recovery else None,
            "started_subscribers": list(self.started_subscribers),
            "failed_subscribers": dict(self.failed_subscribers),
            "started_workers": list(self.started_workers),
            "failed_workers": dict(self.failed_workers),
            "safe_to_trade": self.safe_to_trade,
            "reason": self.reason,
        }


class RuntimeController:
    """
    Coordinates Quanta process boot.

    Correct order:
      1. Check Redis/system registry.
      2. Run RecoveryManager before ticks or execution workers.
      3. Start event subscribers (sanitizer first, hub second).
      4. Start legacy worker threads (pulse last among market stack).
      5. If uncertainty exists, fail closed with kill switch active.
    """

    def __init__(self) -> None:
        self.report = StartupReport()
        self._threads: list[threading.Thread] = []

    def run_recovery(self) -> RecoveryReport:
        """Run crash recovery before any live worker starts."""
        log.info("Runtime startup: running recovery first")
        recovery = RecoveryManager().run()
        self.report.recovery = recovery
        if recovery.kill_switch_was_active:
            self.report.safe_to_trade = False
            self.report.reason = "Kill switch restored from previous run"
        return recovery

    def start_event_subscribers(self) -> None:
        """
        Start in-process event subscribers in dependency order.

        Critical ordering:
          tick_sanitizer starts before market_data_hub, so raw ticks cannot
          be treated as clean data.
        """
        starters: tuple[tuple[str, Callable[[], None]], ...] = (
            ("tick_sanitizer", self._lazy_start("systems.data.tick_sanitizer", "sanitizer")),
            ("market_data_hub", self._lazy_start("systems.data.market_data_hub", "hub")),
            ("data_quality_monitor", self._lazy_start("systems.data.data_quality_monitor", "quality_monitor")),
            ("session_filter", self._lazy_start("systems.intelligence.session_filter", "session_filter")),
            ("regime_detector", self._lazy_start("systems.intelligence.regime_detector", "regime_detector")),
            ("kill_switch", self._lazy_start("risk.kill_switch", "kill_switch")),
            ("correlation_guard", self._lazy_start("risk.correlation_guard", "correlation_guard")),
            ("execution_profiler", self._lazy_start("execution.profiler", "execution_profiler")),
        )

        for name, start_fn in starters:
            try:
                start_fn()
                reg.register_system(name)
                reg.update_system_status(name, "RUNNING", error=None)
                reg.touch_system_heartbeat(name)
                self.report.started_subscribers.append(name)
                log.info("Event subscriber started", component=name)
            except Exception as exc:  # noqa: BLE001 - fail closed, continue report.
                self.report.failed_subscribers[name] = str(exc)
                reg.mark_system_failed(name, str(exc))
                log.exception("Event subscriber failed to start", component=name)

        if self.report.failed_subscribers:
            reason = "One or more event subscribers failed to start"
            state.activate_kill_switch(reason)
            self.report.safe_to_trade = False
            self.report.reason = reason

    def start_workers(self, workers: tuple[tuple[str, Callable[[], None]], ...]) -> None:
        """Start long-running legacy worker threads and register them."""
        for name, fn in workers:
            thread = threading.Thread(
                target=self._worker_main,
                args=(name, fn),
                name=name,
                daemon=False,
            )
            self._threads.append(thread)
            thread.start()
            self.report.started_workers.append(name)
            log.info("Worker thread started", worker=name)

    def mark_running_if_safe(self) -> None:
        """Set final safe-to-trade status after recovery/subscriber startup."""
        if self.report.reason:
            return
        if self.report.recovery and self.report.recovery.kill_switch_was_active:
            self.report.safe_to_trade = False
            self.report.reason = "Kill switch active after recovery"
            return
        self.report.safe_to_trade = not state.is_kill_switch_active()
        self.report.reason = "OK" if self.report.safe_to_trade else "Kill switch active"

    def join_workers(self) -> None:
        """Block until workers stop, then mark statuses on KeyboardInterrupt."""
        try:
            for thread in self._threads:
                thread.join()
        except KeyboardInterrupt:
            log.warning("Runtime interrupted by operator")
            for sys_name in reg.TRACKED_SYSTEMS:
                try:
                    reg.update_system_status(sys_name, "STOPPED", error=None)
                except Exception:
                    pass
            print("\nInterrupted — marking subsystems STOPPED (best effort)")
            print("Goodbye.")
            sys.exit(0)

    def _worker_main(self, name: str, target: Callable[[], None]) -> None:
        """Run one subsystem inside a thread and fail closed on crash."""
        try:
            reg.register_system(name)
            reg.update_system_status(name, "RUNNING", error=None)
            reg.touch_system_heartbeat(name)
            target()
            reg.mark_system_failed(name, f"{name} stopped unexpectedly — check logs and Redis keys")
        except Exception as exc:  # noqa: BLE001
            reg.mark_system_failed(name, str(exc))
            state.activate_kill_switch(f"Worker crashed: {name}")
            self.report.failed_workers[name] = str(exc)
            log.exception("Worker thread crashed", worker=name)

    def start_registry_heartbeat_daemon(self) -> None:
        """
        Periodically refresh Redis heartbeats for subscribers + workers.

        Separates "engine died" from "dashboard-only Streamlit process" where
        singleton._running is meaningless.
        """
        seq = list(self.report.started_subscribers) + list(self.report.started_workers)
        ordered = tuple(dict.fromkeys(seq))
        if not ordered:
            return

        def _loop() -> None:
            while True:
                time.sleep(22)
                for sys_name in ordered:
                    try:
                        reg.touch_system_heartbeat(sys_name)
                    except Exception:
                        pass

        threading.Thread(target=_loop, name="registry_heartbeat_daemon", daemon=True).start()
        log.info("Registry heartbeat daemon started", systems=len(ordered))

    @staticmethod
    def _lazy_start(module_path: str, attr_name: str) -> Callable[[], None]:
        """Return a callable that imports a singleton and calls .start()."""

        def _start() -> None:
            module = __import__(module_path, fromlist=[attr_name])
            singleton = getattr(module, attr_name)
            singleton.start()

        return _start


def check_foundation_or_exit() -> None:
    """Verify Redis/system registry before startup continues."""
    try:
        reg.ping_redis()
        reg.update_phase_status("foundation", "STARTING")
        reg.update_phase_status("foundation", "COMPLETE")
    except Exception as exc:
        print(f"[registry] foundation phase failed: {exc}")
        print("Is Redis running? docker compose -f docker/docker-compose.yml up -d")
        sys.exit(1)


def print_startup_summary(report: StartupReport) -> None:
    """Beginner-friendly startup summary."""
    time.sleep(0.1)
    print("")
    print("PHASE STATUS:")
    for phase_name in reg.TRACKED_PHASES:
        row = reg.get_phase_status(phase_name)
        print(f"  {phase_name} = {row.get('status')}")
    print("")
    for sys_name, row in reg.get_system_status().items():
        print(f"[{sys_name}] {row.get('status')}")
    print("")
    print(f"SAFE TO TRADE: {report.safe_to_trade} ({report.reason})")
    print("--------------------------------")

