"""Phase 2 MT5 Pulse test runner script."""

# We import run_pulse so this script can start the live market data ingestion loop.
from core.pulse import run_pulse


def run_pulse_test():
    """
    Start the pulse engine.

    Notes for beginners:
    - This script will keep running until you stop it (Ctrl + C).
    - It reads MT5 settings from your `.env` file via `core.config`.
    - It writes latest market values into Redis via `core.bus`.
    """

    print("==============================================")
    print("QUANT_ENGINE_2026 | Phase 2 | MT5 Pulse Test")
    print("==============================================")
    print("[INFO] Starting pulse engine (press Ctrl+C to stop).")
    print("[INFO] Ensure MT5 terminal is running and Redis is available.")
    print("")

    # This starts the infinite ingestion loop (1-second updates).
    run_pulse()


if __name__ == "__main__":
    run_pulse_test()

