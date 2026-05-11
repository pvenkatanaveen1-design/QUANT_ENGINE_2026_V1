"""
core/enums.py — Central enum definitions for the entire Quant Forex engine.

WHY THIS FILE EXISTS
--------------------
Raw strings like "BUY", "TREND", "LIVE" scattered across 45 systems are a
maintenance nightmare.  One typo → silent bug that only shows up in live trading.
Define every categorical value here once.  Import from here everywhere else.
Refactoring safe: rename the enum value → your IDE catches every usage.

2026 FOREX REALITY NOTES
-------------------------
- Regime categories reflect real market microstructure:
    STRONG_TREND needs ADX > 30 *and* directional consistency across TFs.
    ADX can be > 25 in a choppy range — always cross-check price structure.
- NEWS_CHAOS is a separate regime (not just a boolean) because it changes
    volatility dynamics completely — spreads widen, fills are unreliable.
- Session enum reflects IST (UTC+5:30) times used by Indian-based traders.
    London DST (Mar-Oct) shifts to 12:00 IST open; winter is 12:30 IST.
    This file uses the NON-DST defaults. clock.py handles DST adjustment.
- SystemMode.EMERGENCY locks out ALL order submission — only manual reset
    in dashboard or Telegram kill switch command can return to DEMO/LIVE.
"""

from enum import Enum


# ─── TRADE DIRECTION ─────────────────────────────────────────────────────────

class Direction(Enum):
    """BUY (long) or SELL (short).  Never use raw strings."""
    BUY  = "BUY"
    SELL = "SELL"


# ─── MARKET REGIME ───────────────────────────────────────────────────────────

class Regime(Enum):
    """
    Market regime classification used by regime/detector.py.

    2026 Reality:
    - STRONG_TREND: ADX > 30, trending candle structure (HH/HL or LL/LH).
      Breakout and momentum strategies shine here.
    - WEAK_TREND: ADX 20-30, some direction but pullbacks are deep.
      Pullback strategies work; breakouts often fail.
    - RANGE: ADX < 20, price oscillating between support and resistance.
      Mean-reversion setups only.  Avoid breakouts.
    - HIGH_VOL: ATR spike > 2× 20-day average.  Usually pre/post news.
      Wide spreads, slippage risk — skip or use wider SL.
    - NEWS_CHAOS: ±30 min around HIGH impact news (CPI, NFP, FOMC).
      Spreads can reach 20-50× normal.  System blocks ALL orders.
    - UNKNOWN: Not enough data (startup, data gap).  Block all trades.
    """
    STRONG_TREND = "STRONG_TREND"
    WEAK_TREND   = "WEAK_TREND"
    RANGE        = "RANGE"
    HIGH_VOL     = "HIGH_VOL"
    NEWS_CHAOS   = "NEWS_CHAOS"
    UNKNOWN      = "UNKNOWN"


# ─── TRADING SESSION ─────────────────────────────────────────────────────────

class Session(Enum):
    """
    Active forex/gold session (IST = UTC+5:30).

    2026 Reality for XAUUSD:
    - ASIA (05:30-12:30 IST): Low liquidity for gold. Spreads 0.5-2.0 pips.
      Most breakouts from Asian range fail.  Better as range-trade only.
    - LONDON (12:30-17:30 IST): Best session for gold.  Highest volume,
      tightest spreads (0.15-0.30 pips).  Most genuine trend moves start here.
    - NEW_YORK (18:30-23:30 IST): Strong continuation or reversal of London
      move.  Spreads widen at 17:30 rollover before NY open.
    - OVERLAP (18:30-20:30 IST): London-NY overlap. Highest volatility window.
      Both institutional flows active simultaneously.  Best for momentum.
    - OFF: No major session.  Skip trading entirely.
    """
    ASIA     = "ASIA"
    LONDON   = "LONDON"
    NEW_YORK = "NEW_YORK"
    OVERLAP  = "OVERLAP"
    OFF      = "OFF"


# ─── NEWS IMPACT ─────────────────────────────────────────────────────────────

class NewsImpact(Enum):
    """
    ForexFactory news impact level.

    2026 Reality:
    - HIGH: Block ±30 min.  NFP, CPI, FOMC, US GDP, Fed speeches.
      Gold can move 20-50 pips in seconds.  No trade is worth this risk.
    - MEDIUM: Monitor only.  Widen SL by 0.5× if holding through.
    - LOW: Safe to trade.  Minor indicators rarely move gold.
    """
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


# ─── TRADE STATUS ────────────────────────────────────────────────────────────

class TradeStatus(Enum):
    """
    Lifecycle state of an order.

    PENDING   → sent to broker, awaiting acknowledgement
    FILLED    → order opened (position is live)
    CLOSED    → position closed (SL/TP/manual/kill)
    CANCELLED → order cancelled before fill (e.g. requote timeout)
    REJECTED  → broker refused the order (off quotes, market closed, margin)
    """
    PENDING   = "PENDING"
    FILLED    = "FILLED"
    CLOSED    = "CLOSED"
    CANCELLED = "CANCELLED"
    REJECTED  = "REJECTED"


# ─── RISK DECISION ───────────────────────────────────────────────────────────

class RiskDecision(Enum):
    """
    Output of the risk engine for each signal.

    APPROVED → all 11 checks passed, lot size calculated, send to executor
    BLOCKED  → at least one check failed, reason stored on RiskCheck.reason
    """
    APPROVED = "APPROVED"
    BLOCKED  = "BLOCKED"


# ─── SYSTEM MODE ─────────────────────────────────────────────────────────────

class SystemMode(Enum):
    """
    Operational mode of the full trading system.

    RESEARCH  — No broker connection.  Offline backtesting and analysis only.
    BACKTEST  — Running historical simulation through backtester.py.
    DEMO      — Connected to live broker on a demo account.  All systems on.
    LIVE      — Connected to funded/real account.  All risk limits enforced.
    EMERGENCY — Kill switch active.  All order submission blocked.
                Only manual dashboard/Telegram reset can leave this mode.

    2026 Note:
    - Never auto-promote from DEMO → LIVE. Require manual confirmation.
    - EMERGENCY mode persists across restarts (stored in state_store SQLite).
    - RESEARCH mode skips Redis, broker bridge, and all real-time feeds.
    """
    RESEARCH  = "RESEARCH"
    BACKTEST  = "BACKTEST"
    DEMO      = "DEMO"
    LIVE      = "LIVE"
    EMERGENCY = "EMERGENCY"


# ─── CLOSE REASON ────────────────────────────────────────────────────────────

class CloseReason(Enum):
    """
    Why a position was closed.  Logged on every TradeEvent for post-analysis.

    2026 Reality:
    - TIME_EXIT: Gold often stalls after 4-6 hours. Time exits prevent
      overnight holds which attract swap costs + gap risk.
    - KILL: System kill switch fired (DD breach or Telegram command).
    - REGIME_CHANGE: Regime flipped — exit immediately to protect edge.
    """
    TAKE_PROFIT   = "TP"
    STOP_LOSS     = "SL"
    MANUAL        = "MANUAL"
    TIME_EXIT     = "TIME_EXIT"
    KILL          = "KILL"
    REGIME_CHANGE = "REGIME_CHANGE"
    BREAKEVEN     = "BREAKEVEN"
