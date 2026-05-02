from __future__ import annotations

import os

CHANNEL_TICKS_PATTERN = os.environ.get("QUANT_CHAN_TICKS_PATTERN", "ticks:*")
CHANNEL_SIGNALS_RAW = os.environ.get("QUANT_CHAN_SIGNALS", "signals:raw")
