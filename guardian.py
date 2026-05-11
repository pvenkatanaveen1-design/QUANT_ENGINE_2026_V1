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
from core.heartbeat import send_telegram  # noqa: E402
from execution.broker_bridge import account_equity  # noqa: E402
from qualifiers.news_filter import passes_news_gate  # noqa: E402
from risk.equity_state import sync_baselines  # noqa: E402
from risk.kill_switch import halt as halt_trading  # noqa: E402
from risk.kill_switch import is_halted  # noqa: E402
from risk.shield import Shield  # noqa: E402

_LOG = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    load_dotenv()

    shield = Shield()
    bus = RedisBus.from_env()
    interval_sec = float(os.environ.get("GUARDIAN_INTERVAL_SEC", "10"))
    telem_sec = float(os.environ.get("GUARDIAN_TELEGRAM_SUMMARY_SEC", "900"))
    symbol_watch = os.environ.get("GUARDIAN_NEWS_SYMBOL", "EURUSD")
    next_telemetry = time.time()

    while True:
        try:
            eq = float(account_equity())
            start_eq, _peak_eq = sync_baselines(bus, eq)
            res = shield.check_drawdowns(start_equity=start_eq, equity=eq)
            news_ok_flag = passes_news_gate(symbol_watch)
            now = time.time()

            if not res.ok:
                halt_trading(bus, res.reason)
                msg = f"Trading halted — {res.reason} | equity={eq:.2f} start={start_eq:.2f}"
                send_telegram(msg)
                _LOG.error(msg)

            if now >= next_telemetry:
                heartbeat_line = (
                    f"heartbeat equity={eq:.2f} start={start_eq:.2f} news_ok={news_ok_flag} halted={int(is_halted(bus))}"
                )
                send_telegram(heartbeat_line)
                next_telemetry = now + telem_sec

            bus.heartbeat("guardian")

        except Exception:
            _LOG.exception("guardian_iteration_failed")

        time.sleep(interval_sec)


if __name__ == "__main__":
    main()
