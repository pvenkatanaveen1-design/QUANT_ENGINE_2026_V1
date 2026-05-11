"""
core/models/risk.py — RiskCheck dataclass.

WHY THIS FILE EXISTS
--------------------
The risk engine produces one RiskCheck per signal.
ALL 11 checks are recorded individually — not just a pass/fail.
This lets you run post-analysis: "how often does news block us vs spread?"
That data guides trading session choices, broker selection, etc.

2026 FOREX REALITY NOTES
-------------------------
- Having 11 individual boolean fields is deliberate.
  In a typical day, you will see:
    - 30-40% of signals blocked by session (outside London/NY)
    - 10-20% blocked by spread (Asian session)
    - 5-15% blocked by news blackout
    - 5-10% blocked by score (weak confluence)
  If spread is blocking > 30% during your target session, you need a better broker.

- equity_at_check is critical for funded accounts.
  Log it so you can replay the risk state at any past moment.

- funded_rules_ok is separate from daily_dd_ok because prop firms may
  have additional rules beyond DD (consistency rule, time-based rules, etc.)
  compliance.py handles the funded-specific checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from core.enums import RiskDecision


@dataclass
class RiskCheck:
    """
    Output of the risk engine for one signal.

    If decision == APPROVED: the lot_size field is filled and the signal
    goes to executor for order submission.

    If decision == BLOCKED: reason explains exactly which check failed.
    The failed_checks() method returns a list of all failing check names.
    """

    # ─── DECISION ────────────────────────────────────────────────────────────
    decision: RiskDecision = RiskDecision.BLOCKED
    reason: str = ""              # First failure reason — human readable

    # ─── INDIVIDUAL CHECK RESULTS ────────────────────────────────────────────
    # Each check is a boolean.  False = failed.
    # Order matches the check execution order in the risk engine.
    kill_switch_ok:   bool = False   # 1. Kill switch not active (checked first — fastest)
    funded_rules_ok:  bool = False   # 2. Prop firm compliance (before everything else)
    session_ok:       bool = False   # 3. Within allowed trading session
    news_ok:          bool = False   # 4. No news blackout active
    regime_ok:        bool = False   # 5. Regime allows this strategy
    spread_ok:        bool = False   # 6. Spread within limit
    score_ok:         bool = False   # 7. Confluence score above minimum
    daily_dd_ok:      bool = False   # 8. Daily DD not reached
    total_dd_ok:      bool = False   # 9. Total DD not reached
    trade_count_ok:   bool = False   # 10. Max trades today not reached
    correlation_ok:   bool = False   # 11. USD correlated exposure within limit

    # ─── ACCOUNT STATE AT CHECK TIME ─────────────────────────────────────────
    # These are snapshots for audit trail — do not use for live calculations.
    equity_at_check: float  = 0.0    # Account equity when risk check ran
    balance_at_check: float = 0.0    # Account balance at check time
    daily_dd_pct: float     = 0.0    # Daily drawdown % at check time (e.g. 0.02 = 2%)
    total_dd_pct: float     = 0.0    # Total drawdown % from peak
    trades_today: int       = 0      # Number of trades already taken today
    lot_size: float         = 0.0    # Calculated lot size if approved (0.0 if blocked)

    # ─── TIMESTAMP AND TRACE ─────────────────────────────────────────────────
    timestamp: datetime = field(default_factory=datetime.utcnow)
    correlation_id: str = ""         # Links to SignalEvent

    # ─── ANALYSIS HELPERS ────────────────────────────────────────────────────

    def all_clear(self) -> bool:
        """
        Returns True only when every single check passed.
        Use this to set decision = APPROVED.
        """
        return all([
            self.kill_switch_ok,
            self.funded_rules_ok,
            self.session_ok,
            self.news_ok,
            self.regime_ok,
            self.spread_ok,
            self.score_ok,
            self.daily_dd_ok,
            self.total_dd_ok,
            self.trade_count_ok,
            self.correlation_ok,
        ])

    def failed_checks(self) -> list[str]:
        """
        Return list of check names that failed.
        Useful for: logging, dashboard, post-analysis.
        Example: ["news", "spread"] means news blackout AND spread too wide.
        """
        checks = {
            "kill_switch":   self.kill_switch_ok,
            "funded_rules":  self.funded_rules_ok,
            "session":       self.session_ok,
            "news":          self.news_ok,
            "regime":        self.regime_ok,
            "spread":        self.spread_ok,
            "score":         self.score_ok,
            "daily_dd":      self.daily_dd_ok,
            "total_dd":      self.total_dd_ok,
            "trade_count":   self.trade_count_ok,
            "correlation":   self.correlation_ok,
        }
        return [name for name, passed in checks.items() if not passed]

    def passed_checks(self) -> list[str]:
        """Return list of check names that passed.  Complement of failed_checks()."""
        checks = {
            "kill_switch":   self.kill_switch_ok,
            "funded_rules":  self.funded_rules_ok,
            "session":       self.session_ok,
            "news":          self.news_ok,
            "regime":        self.regime_ok,
            "spread":        self.spread_ok,
            "score":         self.score_ok,
            "daily_dd":      self.daily_dd_ok,
            "total_dd":      self.total_dd_ok,
            "trade_count":   self.trade_count_ok,
            "correlation":   self.correlation_ok,
        }
        return [name for name, passed in checks.items() if passed]

    def block_rate(self) -> float:
        """
        Fraction of checks that failed (0.0 = all pass, 1.0 = all fail).
        Used in dashboard to show how 'close' to trading conditions are.
        """
        failed = len(self.failed_checks())
        total = 11  # Total number of checks
        return round(failed / total, 2)

    def __repr__(self) -> str:
        failed = self.failed_checks()
        return (
            f"RiskCheck({self.decision.value} "
            f"failed={failed if failed else 'none'} "
            f"lot={self.lot_size:.2f} "
            f"daily_dd={self.daily_dd_pct:.1%} "
            f"total_dd={self.total_dd_pct:.1%})"
        )
