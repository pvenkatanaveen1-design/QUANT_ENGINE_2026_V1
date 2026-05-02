from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_LOG = logging.getLogger(__name__)


def _spawn(label: str, argv: list[str]) -> subprocess.Popen[str]:
    _LOG.info("launch_%s argv=%s", label, argv)
    return subprocess.Popen(
        argv,
        cwd=str(_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        encoding="utf-8",
        text=True,
        close_fds=os.name != "nt",
    )


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    py = sys.executable

    steps: list[tuple[str, list[str]]] = [
        ("pulse", [py, str(_ROOT / "core" / "pulse.py")]),
        ("guardian", [py, str(_ROOT / "guardian.py")]),
        ("brain", [py, str(_ROOT / "brain.py")]),
        ("executor", [py, str(_ROOT / "executor.py")]),
    ]

    procs: list[tuple[str, subprocess.Popen[str], list[str]]] = []

    stagger = float(os.environ.get("WATCHDOG_STAGGER_SEC", "2.0"))
    for label, argv in steps:
        proc = _spawn(label, argv)
        procs.append((label, proc, argv))
        time.sleep(stagger)

    retry_backoff = float(os.environ.get("WATCHDOG_RESTART_BACKOFF_SEC", "5.0"))
    poll_interval = float(os.environ.get("WATCHDOG_POLL_SEC", "3.0"))

    try:
        while True:
            time.sleep(poll_interval)
            for idx, (label, proc, argv) in enumerate(list(procs)):
                if proc.poll() is None:
                    continue
                _LOG.error("process_exited label=%s exit_code=%s", label, proc.returncode)
                time.sleep(retry_backoff)
                new_proc = _spawn(label, argv)
                procs[idx] = (label, new_proc, argv)
    except KeyboardInterrupt:
        _LOG.warning("watchdog_interrupt")
        for _, proc, _argv in procs:
            proc.terminate()


if __name__ == "__main__":
    main()
