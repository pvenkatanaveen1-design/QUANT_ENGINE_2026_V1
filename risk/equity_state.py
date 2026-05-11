from __future__ import annotations


def sync_baselines(bus, equity_now: float) -> tuple[float, float]:
    start_str = bus.get_str("quant:start_equity")
    peak_str = bus.get_str("quant:peak_equity")
    start_eq = float(start_str) if start_str else float(equity_now)
    peak_eq = float(peak_str) if peak_str else float(equity_now)
    peak_eq = max(peak_eq, equity_now)
    bus.set_str("quant:start_equity", f"{start_eq:.8f}")
    bus.set_str("quant:peak_equity", f"{peak_eq:.8f}")
    return start_eq, peak_eq
