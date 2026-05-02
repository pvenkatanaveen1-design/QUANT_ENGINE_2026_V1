from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.paths import project_root


@dataclass(frozen=True)
class ShieldResult:
    ok: bool
    reason: str


class Shield:
    def __init__(self, risk_limits_path: Path | None = None) -> None:
        path = risk_limits_path or (project_root() / "config" / "risk_limits.yaml")
        self._limits: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    @property
    def limits(self) -> dict[str, Any]:
        return dict(self._limits)

    def check_drawdowns(self, start_equity: float, equity: float) -> ShieldResult:
        if start_equity <= 0:
            return ShieldResult(True, "no_start_equity")
        total_dd_pct = (start_equity - equity) / start_equity * 100.0
        max_total = float(self._limits.get("max_total_drawdown_pct", 10.0))
        if total_dd_pct >= max_total:
            return ShieldResult(False, f"max_total_drawdown breached {total_dd_pct:.2f}% >= {max_total}%")

        kill_level = float(self._limits.get("kill_switch_equity_level", 0.0))
        if kill_level > 0.0 and equity <= kill_level:
            return ShieldResult(False, f"kill_switch_equity_level {equity} <= {kill_level}")

        return ShieldResult(True, "ok")
