# Regime V2 Stabilization Change Log (2026-05-11)

## What changed
- Extended `regime_detector` from single-factor coarse classification to multi-factor 12-state classification with:
  - ADX + ATR voting
  - structure classifier
  - session-aware weighting
  - probability output per regime
  - transition-state output
  - strategy mapping output
- Preserved backward compatibility by continuing to emit legacy coarse `Regime` enum while adding `regime_label` and probability metadata.
- Added new intelligence events:
  - `REGIME_PROBABILITY`
  - `REGIME_TRANSITION`
- Added runtime registry + Redis operational keys for regime waiting/health:
  - `regime:waiting_reason`
  - `regime:current_label`
  - `regime:transition_state`
  - `regime:last_update_ts`
- Upgraded regime persistence in `RegimeRepository`:
  - schema extension support for new columns
  - regime distribution by lookback years
  - regime performance analytics (up to yesterday) from closed trades
- Upgraded `Regime Monitor` page:
  - symbol/timeframe/lookback selector
  - standardized page-state banner
  - dependency-chain health block
  - 12-state regime + confidence + indicator breakdown
  - transition + strategy enable/disable visibility
  - probability distribution
  - historical distribution and performance table
- Added new `regime/` module set for safer extensibility:
  - `classifiers/adx_classifier.py`
  - `classifiers/atr_classifier.py`
  - `classifiers/structure_classifier.py`
  - `classifiers/session_classifier.py`
  - `classifiers/volume_classifier.py`
  - `classifiers/momentum_classifier.py`
  - `regime_voter.py`, `regime_validator.py`, `transition_engine.py`,
    `probability_engine.py`, `strategy_mapping.py`
  - `regime_history.py`, `regime_statistics.py`, `regime_performance.py`,
    `regime_cache.py`, `regime_framework.py`
- Added extended lookback controls to Regime Monitor:
  - 1 day, 3 days, 1 week, 2 weeks, 1/2/3/6 months, 1/2/3/5/10 years, custom date range
- Added cached historical analytics rendering in dashboard (`st.cache_data`) for low-CPU VPS-safe behavior.
- Added transition-frequency analytics and regime drill-down table in Regime Monitor.
- Added `Regime Config Lab` in dashboard Config Editor with:
  - safe sliders/inputs per classifier and engine component
  - allowed/recommended/default/warning bands
  - runtime metric visibility and source labels
  - validation before save
  - reset defaults, snapshot rollback, export, diff compare
  - audit trail (`logs/regime_config_audit.jsonl`)
- Added structured regime config files:
  - `config/regime/adx.yaml`
  - `config/regime/atr.yaml`
  - `config/regime/structure.yaml`
  - `config/regime/probability.yaml`
  - `config/regime/strategy_mapping.yaml`
- Runtime now reads regime thresholds/weights from YAML-backed `regime/runtime_config.py`
  instead of relying on deep hardcoded values only.

## Why changed
- Improve operational clarity when detector is waiting vs degraded vs active.
- Support funded-account safety by avoiding unstable/fake classifications.
- Provide richer regime context for strategy filtering without changing core architecture.

## Affected systems
- `systems/intelligence/regime_detector.py`
- `core/models/regime.py`
- `core/event_bus.py`
- `repositories/regime_repository.py`
- `dashboard/runtime_status.py`
- `dashboard/dashboard_app.py`
- `regime/*` (new)
- `docs/modules/systems_intelligence.txt`
- `docs/SYSTEM_OVERVIEW.txt`
- `docs/architecture.txt`

## Restart requirements
- Restart `python run.py` to activate detector logic/event changes.
- Restart Streamlit dashboard to load new Regime Monitor UI.

## Migration requirements
- No manual migration required.
- DuckDB schema extension is attempted automatically by repository writes in engine RW mode.
- Dashboard in read-only mode remains functional; it only reads available columns.

## Runtime impact
- Classification still runs on `CANDLE_CLOSED` (H1 requirement preserved).
- Non-H1 events are ignored.
- Detector now emits probabilities and transition diagnostics.
- Waiting reasons are explicit when candle history is insufficient.

## UI impact
- Regime Monitor now explains unavailable states and dependency health explicitly.
- Operator can choose lookback years for historical analytics.
- Active strategies per regime are displayed with rationale.

## Fallback behavior
- If DuckDB history is unavailable/locked, detector falls back to hub candle access.
- If history is insufficient, detector sets warning state and waiting reason (no forced regime).
