"""Heartbeat engine test runner script."""

# We import run_heartbeat so this script starts the health monitoring loop.
from core.heartbeat import run_heartbeat


def run_heartbeat_test():
    """
    Start the heartbeat engine.

    Notes for beginners:
    - This script runs until you stop it (Ctrl + C in the terminal).
    - It reads pulse, MT5, market, and clock keys from Redis via core.bus.
    - It writes heartbeat:* keys every 5 seconds so other parts of the system can see health.
    - Redis must be running (see .env for REDIS_HOST / REDIS_PORT).
    """

    print("==============================================")
    print("QUANT_ENGINE_2026 | Heartbeat Test")
    print("==============================================")
    print("[INFO] Starting heartbeat engine (press Ctrl+C to stop).")
    print("[INFO] Expect heartbeat:* keys in Redis to update every 5 seconds.")
    print("[INFO] Seed Redis with pulse:status, mt5:connection, market:XAUUSD:timestamp,")
    print("       and clock:status for meaningful overall health.")
    print("")

    # This starts the infinite loop defined in core.heartbeat.run_heartbeat.
    run_heartbeat()


if __name__ == "__main__":
    run_heartbeat_test()
