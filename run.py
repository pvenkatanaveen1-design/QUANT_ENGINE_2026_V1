import subprocess
import sys
import threading
import time


def run_engine():
    subprocess.run([sys.executable, "simple_main.py"])


def run_dashboard():
    subprocess.run(["streamlit", "run", "dashboard_app.py", "--server.port=8501"])


if __name__ == "__main__":
    engine_thread = threading.Thread(target=run_engine, daemon=True)
    engine_thread.start()
    time.sleep(3)
    run_dashboard()
