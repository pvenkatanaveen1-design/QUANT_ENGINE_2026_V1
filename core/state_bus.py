from pathlib import Path
import json
import time

STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)


def write_state(key: str, data: dict):
    path = STATE_DIR / f"{key}.json"
    path.write_text(json.dumps(data, indent=2, default=str))


def write_system_state(data: dict):
    """Canonical JSON snapshot for the candle-only runtime (`simple_main.py`)."""
    path = STATE_DIR / "system_state.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    # Back-compat: root Streamlit dashboard still reads `state/system.json`.
    write_state("system", data)


def read_state(key: str) -> dict:
    path = STATE_DIR / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def write_signal(signal_dict: dict):
    write_state("current_signal", signal_dict)


def read_signal() -> dict:
    return read_state("current_signal")


def set_kill(reason: str):
    write_state("kill_switch", {"active": True, "reason": reason, "time": time.time()})


def clear_kill():
    write_state("kill_switch", {"active": False})


def is_kill_active() -> bool:
    data = read_state("kill_switch")
    return data.get("active", False)


def get_kill_reason() -> str:
    return read_state("kill_switch").get("reason", "")


def write_heartbeat(component: str):
    write_state(f"hb_{component}", {"time": time.time(), "component": component})

