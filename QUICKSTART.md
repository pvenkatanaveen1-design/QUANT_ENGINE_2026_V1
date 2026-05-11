# QUANT ENGINE 2026 — Quick start

This stack runs as a **single Python process** with **MetaTrader 5** for data and demo execution, **YAML** for configuration, and **JSON/CSV** for state and logs. It does **not** use Redis.

## 1. Install

From the `QUANT_ENGINE_2026` directory:

```bash
pip install -r requirements.txt
```

Optional: copy `.env` and set `SYMBOL_DEFAULT=XAUUSD` (or your symbol).

## 2. MetaTrader 5

1. Install the MetaTrader 5 terminal.
2. Log in to a **demo** account (the executor refuses live unless you change the funded-account / demo checks in code and config intentionally).
3. Leave the terminal running while the engine is active.

## 3. Run the engine

From `QUANT_ENGINE_2026`:

```bash
python main.py
```

The loop waits **15 minutes** between cycles, fetches M15/H1 data, detects regime, scores signals, and may send orders on **demo** only. It writes:

- `state/system_state.json` — last cycle snapshot (dashboard reads this)
- `state/kill_switch.json` — emergency stop flag
- `logs/trades.csv` — trade log

## 4. Run the dashboard

In a second terminal, from `QUANT_ENGINE_2026`:

```bash
streamlit run dashboard_app.py
```

Open the URL shown in the terminal (typically **http://localhost:8501**). The dashboard only reads JSON, YAML, and CSV under this folder; it does **not** import `core` trading modules.

## 5. Configuration

- `config/regimes.yaml` — regime thresholds and session windows
- `config/strategies.yaml` — strategies and `enabled` flags
- `config/risk.yaml` — risk limits and sizing multipliers

You can edit these by hand or use the sidebar controls in the Streamlit app (save buttons write YAML).

## 6. Stop safely

Stop `main.py` with Ctrl+C. To block new trades without stopping the process, activate the kill switch via the dashboard or write `state/kill_switch.json` with `"active": true`.

## 7. Fifty-two regime taxonomy (UI)

Open **Regime laboratory** tab in `dashboard_app.py`. Catalog: `config/regime_taxonomy.yaml` (quadrant mapping, literature defaults, top-15 groups). Calibration save path: `state/regime_lab_calibration.yaml`. OHLC-detectable rules run in-engine; macro/COT/VIX rows need future data hooks.

## 8. Orders vs backtest (MT5)

- **Place orders (demo):** `main.py` uses MT5 live ticks and `core/executor.py` to send market orders when risk checks pass. You need the terminal logged in; this is **not** the built-in MT5 Strategy Tester (that is MQL5/EAs inside the terminal).
- **Backtest regime only (Python):** walk-forward regime labels on history from MT5 (no orders):

```bash
python run_regime_backtest.py
```

This writes `logs/regime_backtest_walk.csv` and prints a short tail. Use `--step 4` to sample every fourth M15 bar for a quicker run.

**Prefer the dashboard:** open Streamlit → **Operator console** → **Run regime backtest** (same logic; no separate script needed).

The older `systems/research/backtester.py` path is a separate, strategy-class-based simulator (often DuckDB/CSV data); the script above uses the **same** `detect_regime` logic as `main.py`, with bar times driving the session voter.
