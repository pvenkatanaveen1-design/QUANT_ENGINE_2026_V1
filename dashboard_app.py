from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.bus import RedisBus  # noqa: E402

_LOG = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    load_dotenv()
    bus = RedisBus.from_env()
    interval = float(os.environ.get("DASH_POLL_SEC", "10.0"))
    pattern = os.environ.get("QUANT_HB_PATTERN", "quant:hb:*")

    while True:
        n = bus.scan_count_pattern(pattern)
        kill = bus.get_str("quant:kill_switch")
        reason = bus.get_str("quant:kill_reason")
        _LOG.info("heartbeat_keys=%s kill_switch=%s reason=%s", n, kill, reason)
        time.sleep(interval)


if __name__ == "__main__":
    main()
