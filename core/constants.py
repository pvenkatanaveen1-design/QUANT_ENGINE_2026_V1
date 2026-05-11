"""
core/constants.py — All hardcoded values in one place.

WHY THIS FILE EXISTS
--------------------
Magic numbers scattered across 45 files = unmaintainable system.
Change a threshold here → changes everywhere that imports it.
Constants are DEFAULTS only — YAML config overrides them at runtime.

2026 FOREX REALITY NOTES
-------------------------
Every default here is based on live funded account experience with XAUUSD:

SPREAD REALITY (XAUUSD, ECN broker, 2026):
  London session:  0.15 - 0.30 pips  (tightest)
  NY session:      0.20 - 0.50 pips
  NY/London overlap: 0.15 - 0.25 pips (excellent)
  Asia session:    0.40 - 1.50 pips
  Off session:     1.00 - 5.00 pips
  Around news:     5.00 - 50.00 pips (broker-widened deliberately)
  Rollover 17:00 UTC: 2.00 - 10.00 pips for ~5 minutes

  DEFAULT_MAX_SPREAD = 1.5 pips is aggressive but achievable in London/NY.
  If your fills are getting blocked too often, raise to 2.0 in YAML.

ATR REALITY (XAUUSD H1, 2026):
  Quiet days:   10-15 pips
  Normal days:  15-25 pips
  High vol days: 30-60 pips
  NFP/CPI days: 80-150+ pips spike

  ATR_SL_MULTIPLIER = 1.5 means:
    Normal day: SL = 22 pips.  Good.
    High vol day: SL = 45 pips.  Wide but justified.
    Do NOT trade on NFP day with a tight SL — you will get stopped out.
"""

# ─── SYMBOLS ─────────────────────────────────────────────────────────────────
# Start with XAUUSD only for at least 30 live trades.
# Gold has unique characteristics: correlated with DXY, US yields, geopolitics.
# Add FX pairs (EURUSD, GBPUSD) only after your XAUUSD system is proven.
SYMBOLS        = ["XAUUSD"]
PRIMARY_SYMBOL = "XAUUSD"

# ─── TIMEFRAMES ──────────────────────────────────────────────────────────────
# Used as string keys for MT5 calls and DuckDB table names.
TF_M1  = "M1"
TF_M5  = "M5"
TF_M15 = "M15"
TF_H1  = "H1"    # PRIMARY signal timeframe — best balance of noise/signal
TF_H4  = "H4"    # Structure / trend direction confirmation
TF_D1  = "D1"    # Major support/resistance levels

# ─── SESSION TIMES IST (UTC+5:30) ────────────────────────────────────────────
# core/clock.py adjusts these for DST automatically.
# LONDON_OPEN_IST changes to 12:00 during BST (March-October).
LONDON_OPEN_IST   = (12, 30)   # (hour, minute) IST, non-DST
LONDON_CLOSE_IST  = (17, 30)
NY_OPEN_IST       = (18, 30)
NY_CLOSE_IST      = (23, 30)
OVERLAP_START_IST = (18, 30)   # London+NY overlap — strongest institutional flow
OVERLAP_END_IST   = (20, 30)
ASIA_OPEN_IST     = (5,  30)
ASIA_CLOSE_IST    = (12, 30)

# ─── RISK DEFAULTS ───────────────────────────────────────────────────────────
# These are DEFAULTS only.  config/risk_rules.yaml overrides per account.

# 0.5% per trade on a $10K account = $50 max loss per trade.
# This is the correct starting point for funded accounts.
# Do NOT raise above 1.0% until 50+ profitable trades proven.
DEFAULT_RISK_PCT = 0.005

# 4% daily drawdown hard kill.
# FTMO limit is 5%. We set ours at 4% to give a 1% safety buffer.
# This means if we lose $400 on a $10K account, NO MORE TRADES TODAY.
DEFAULT_MAX_DAILY_DD = 0.04

# 10% total (from peak equity).
# Match your prop firm's rule exactly.
# FTMO = 10%, E8 = 8%.  Set to your firm's limit minus 1% buffer.
DEFAULT_MAX_TOTAL_DD = 0.10

# Max 3 trades per day keeps you from revenge trading after losses.
# Studies show: after 2 consecutive losses, win rate drops on trade 3.
# Stick to 2-3 maximum for the first 3 months.
DEFAULT_MAX_TRADES_DAY = 3

# Signal must score ≥ 60/100 on confluence scoring engine.
# Below 60 = too many conditions are weak.  Skip the trade.
# Raise to 70 once your scoring engine is calibrated on 100+ backtested trades.
DEFAULT_MIN_SCORE = 60

# London session max spread: 1.5 pips for XAUUSD with a good ECN broker.
# If you are getting blocked frequently, check your broker's spread during your session.
# Raise to 2.0 only if your broker consistently shows 1.6-1.8 in London.
DEFAULT_MAX_SPREAD = 1.5      # pips

# Maximum overnight swap cost to allow holding a position.
# XAUUSD short swap can be VERY negative (−$5 to −$15 per 0.10 lot per night).
# If holding swing trades, verify swap before entry: check broker swap rates.
DEFAULT_MAX_SWAP_USD = 10.0   # USD per lot per night

# ─── PROP FIRM RULE PRESETS ──────────────────────────────────────────────────
# These match funded firm rules as of 2026.  Verify current rules before buying.
# Source: ftmo.com, e8funding.com, the5ers.com

FTMO_DAILY_DD  = 0.05    # 5% from start-of-day balance
FTMO_MAX_DD    = 0.10    # 10% from initial balance (not peak)

E8_DAILY_DD    = 0.05    # 5%
E8_MAX_DD      = 0.08    # 8%

THE5ERS_MAX_DD = 0.06    # Varies by plan — verify current terms

# ─── TECHNICAL INDICATOR DEFAULTS ────────────────────────────────────────────
# Based on standard forex practice + XAUUSD backtesting.

ATR_PERIOD         = 14     # Standard Wilder ATR.  Works well across all TFs.
ATR_SL_MULTIPLIER  = 1.5    # SL = 1.5 × ATR(14).  Tight but above intraday noise.
                             # For volatile days, ATR expands naturally so SL widens.
RR_MINIMUM         = 2.0    # Minimum risk/reward.  2R needed for 40% win rate to be profitable.
                             # Formula: win_rate × RR - (1 - win_rate) > 0
                             # At 40% WR and 2R: 0.40×2 - 0.60×1 = +0.20 positive expectancy.

ADX_STRONG_THRESH  = 30     # ADX > 30 = STRONG_TREND (not 25 — 25 is weak signal)
ADX_TREND_THRESH   = 25     # ADX 25-30 = WEAK_TREND
ADX_RANGE_THRESH   = 20     # ADX < 20 = RANGE

ATR_HIGH_VOL_MULT  = 2.0    # ATR > 2× 20-period avg = HIGH_VOL regime
ATR_LOOKBACK       = 20     # Period for ATR percentile calculation

NEWS_BLOCK_MINS    = 30     # Block ±30 min around HIGH impact events.
                             # Most prop firms require this; some require ±15 min.
                             # FTMO has had issues with traders who ignored news windows.

# ─── XAUUSD-SPECIFIC CONSTANTS ───────────────────────────────────────────────
# These only apply to gold.  FX pairs will need their own in symbols.yaml.

XAUUSD_PIP_VALUE      = 0.01       # 1 pip = $0.01 per ounce = $1 per standard lot
XAUUSD_MIN_LOT        = 0.01       # Minimum tradeable lot
XAUUSD_LOT_STEP       = 0.01       # Lot size increments
XAUUSD_CONTRACT_SIZE  = 100        # 100 oz per standard lot
XAUUSD_TICK_SIZE      = 0.01       # Minimum price movement

# ─── DRAWDOWN CALCULATION METHOD ─────────────────────────────────────────────
# IMPORTANT: Different prop firms measure DD differently.
# FTMO: Daily DD = from *balance* at day start, NOT from peak equity.
#        Total DD = from *initial deposit*, NOT from peak.
# The5ers: From *peak balance* (harder rule — be careful).
# This engine defaults to FTMO-style. Change in funded_rules.yaml.
DD_FROM_PEAK    = False   # True = from peak equity (harder), False = from day start
DD_FROM_BALANCE = True    # Use balance not equity for daily DD calculation

# ─── PATHS ───────────────────────────────────────────────────────────────────
DUCKDB_PATH    = "data/market.duckdb"      # Historical OHLCV, ticks
SQLITE_PATH    = "data/journal.db"         # Trade journal (SQLite)
LOG_DIR        = "logs/"
DATA_RAW_DIR   = "data/raw/"
DATA_CLEAN_DIR = "data/cleaned/"
CONFIG_DIR     = "config/"

# ─── LOG FILE PATHS (per category) ───────────────────────────────────────────
# Each category writes JSON Lines to its own file AND to common/all.log.
# Import from core.logger for the actual file Path objects at runtime.
LOG_FILES = {
    "all":         "logs/common/all.log",      # master — every entry
    "system":      "logs/system.log",          # startup, shutdown, health
    "data":        "logs/data.log",            # ticks, candles, data quality
    "trading":     "logs/trading.log",         # signals, orders, fills, closes
    "risk":        "logs/risk.log",            # DD, spread, kill switch
    "execution":   "logs/execution.log",       # MT5 requests, broker responses
    "ui":          "logs/ui.log",              # dashboard actions, config edits
    "dependency":  "logs/dependency.log",      # Redis, MT5, DuckDB failures
    "error":       "logs/error.log",           # all exceptions (also mirrored here)
    "audit":       "logs/audit.log",           # funded rule decisions, overrides
    "performance": "logs/performance.log",     # slow ops, latency warnings
    "recovery":    "logs/recovery.log",        # restarts, reconciliation
    "backtest":    "logs/backtest.log",        # backtest runs, WF, Monte Carlo
}

# ─── TIMING ──────────────────────────────────────────────────────────────────
SHIELD_INTERVAL_SECONDS  = 1      # How often shield checks equity (was 5s, now 1s)
HEARTBEAT_INTERVAL_SECS  = 5      # Health check frequency
CLOCK_UPDATE_SECS        = 10     # Session/time update frequency
PULSE_TICK_INTERVAL_SECS = 1      # Tick polling from MT5
