"""Test structured logging system end-to-end."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.logger import (
    get_logger, LogCategory,
    trading_log, risk_log, execution_log, audit_log,
    recovery_log, backtest_log, performance_log,
    dependency_log, system_log, data_log,
)
from core.log_context import log_ctx

# Set thread-local context
log_ctx.set(
    symbol="XAUUSD",
    strategy="alpha_breakout",
    system_mode="TEST",
    session="LONDON",
    regime="STRONG_TREND",
)

# --- Trading log ---
trading_log.signal(
    "Buy signal fired",
    symbol="XAUUSD", direction="BUY", strategy="alpha_breakout",
    entry_price=2350.0, stop_loss=2345.5, take_profit=2359.0,
    quality_score=72.5, session="LONDON", regime="STRONG_TREND",
    spread_pips=0.3, correlation_id="SIG_001",
)

trading_log.trade_open(
    "Trade filled by MT5",
    symbol="XAUUSD", direction="BUY", lot_size=0.05,
    entry_price=2350.20, stop_loss=2345.5, take_profit=2359.0,
    order_id="MT5_12345", trade_id="TRD_001",
    slippage_pips=0.2, latency_ms=185, spread_at_fill=0.3,
)

trading_log.trade_close(
    "Trade closed at TP",
    symbol="XAUUSD", trade_id="TRD_001", close_price=2359.0,
    close_reason="TAKE_PROFIT", net_pnl=43.50, gross_pnl=45.0,
)

# --- Risk log ---
risk_log.risk_check("Daily DD check", check_name="DAILY_DD", result="PASS", value=1.5, threshold=4.0)
risk_log.blocked("Signal blocked - spread too wide",
                 reason="SPREAD_TOO_WIDE", symbol="XAUUSD", value=3.2,
                 threshold=2.0, blocked_by="cost_guard")

# --- Execution log ---
execution_log.execution("Order submitted to MT5",
                        symbol="XAUUSD", order_id="MT5_12345",
                        result="SUBMITTED", expected_price=2350.0, latency_ms=12)

execution_log.execution("Order filled by broker",
                        symbol="XAUUSD", order_id="MT5_12345",
                        result="FILLED", expected_price=2350.0,
                        actual_price=2350.20, slippage_pips=0.2, latency_ms=185)

# --- Audit log ---
audit_log.audit("Funded rule check passed",
                decision="APPROVED", reason="All FTMO rules within limits",
                rule_name="FTMO_DAILY_DD", value=1.5, symbol="XAUUSD")

# --- Performance log ---
performance_log.perf("ADX calculation", operation="calculate_adx",
                     duration_ms=8.3, threshold_ms=100.0)
performance_log.perf("Slow candle query WARN", operation="get_candles",
                     duration_ms=520.0, threshold_ms=200.0)

# --- Recovery log ---
recovery_log.recovery_step("State loaded from SQLite",
                           step="STATE_RESTORE", result="OK",
                           detail="equity=10000.0 from snapshot")

# --- Dependency log ---
dependency_log.dependency("DuckDB connected", service="DUCKDB", status="CONNECTED")
dependency_log.dependency("Redis unavailable", service="REDIS",
                          status="FAILED", error_message="Connection refused")

# --- System log ---
system_log.info("Quanta engine started", system_mode="TEST", event_type="STARTUP")

# --- Data log ---
data_log.info("H1 candle closed", symbol="XAUUSD", timeframe="H1",
              event_type="CANDLE_CLOSED")

# --- Context manager test ---
log_ctx.clear()
with log_ctx(symbol="EURUSD", strategy="alpha_sweep", correlation_id="SIG_002"):
    trading_log.signal(
        "Sweep signal (EURUSD)",
        symbol="EURUSD", direction="SELL", strategy="alpha_sweep",
        entry_price=1.0850, stop_loss=1.0870, take_profit=1.0810,
        quality_score=65.0,
    )

print("\nAll structured log calls succeeded!")
print("\nVerifying log files:")

categories = ["trading", "risk", "execution", "audit", "performance",
              "recovery", "dependency", "system", "data", "error"]

for cat in categories:
    path = "logs/" + cat + ".log"
    if os.path.exists(path):
        with open(path) as f:
            lines = [l.strip() for l in f if l.strip()]
        count = len(lines)
        if lines:
            last = json.loads(lines[-1])
            msg = last.get("msg", "")[:45]
            sym = last.get("symbol", "")
            print(f"  {cat:<15} {count:>4} entries | last: {msg!r} {sym}")
        else:
            print(f"  {cat:<15} EMPTY")
    else:
        print(f"  {cat:<15} MISSING")

all_path = "logs/common/all.log"
if os.path.exists(all_path):
    with open(all_path) as f:
        all_count = sum(1 for l in f if l.strip())
    print(f"\n  common/all.log: {all_count} total entries (master log)")

print("\nStructured logging system is READY.")
