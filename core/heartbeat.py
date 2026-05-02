from __future__ import annotations

import logging
import os
from typing import Final

import requests

_LOG = logging.getLogger(__name__)
DEFAULT_TIMEOUT_SEC: Final = 12


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN") or ""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat_id:
        _LOG.warning("Telegram not configured")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=DEFAULT_TIMEOUT_SEC,
        )
        r.raise_for_status()
        return True
    except requests.RequestException:
        _LOG.exception("telegram_send_failed")
        return False
