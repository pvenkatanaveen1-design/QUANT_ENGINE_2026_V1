Regime Intelligence (V3 Extension)

Purpose:
- Provide multi-factor regime intelligence and historical regime analytics
- Keep existing runtime architecture intact (event-driven, H1 candle based)

Core runtime flow:
MT5 -> Pulse -> Tick Sanitizer -> Market Data Hub -> CANDLE_CLOSED(H1)
-> RegimeDetector -> REGIME_CHANGED / REGIME_PROBABILITY / REGIME_TRANSITION
-> Strategy Selector -> Risk -> Execution

This package adds:
- Classifiers (ADX/ATR/Structure/Session/Volume/Momentum)
- Regime voter
- Validator (confidence + persistence)
- Transition engine
- Probability engine
- Strategy mapping rules
- Lightweight analytics helpers for dashboard

