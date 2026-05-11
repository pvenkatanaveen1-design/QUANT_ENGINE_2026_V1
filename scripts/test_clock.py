"""Phase 2 Clock test runner script."""

# We import run_clock so this script can start the trading clock engine loop.
from core.clock import run_clock


def run_clock_test():
    """
    Start the clock engine.

    Notes for beginners:
    - This script keeps running until you stop it (Ctrl + C).
    - It publishes UTC/IST/session values to Redis every 10 seconds.
    - It helps verify market-time and session logic quickly.
    """

    print("================================================")
    print("QUANT_ENGINE_2026 | Phase 2 | Trading Clock Test")
    print("================================================")
    print("[INFO] Starting clock engine (press Ctrl+C to stop).")
    print("[INFO] Ensure Redis is running before starting this script.")
    print("")

    # This starts the continuous 10-second clock publishing loop.
    run_clock()


if __name__ == "__main__":
    run_clock_test()

