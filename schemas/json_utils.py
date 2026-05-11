from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_default)


def loads(s: str) -> Any:
    return json.loads(s)


def _json_default(o: Any) -> str:
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"Object not JSON serializable: {type(o)!r}")
