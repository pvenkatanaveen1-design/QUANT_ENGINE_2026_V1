Run Scripts
===========

Location:
  QUANT_ENGINE_2026/run-scripts/

Files:
  - start_all.ps1   -> starts docker(redis/postgres) + run.py + streamlit
  - stop_all.ps1    -> stops run.py/streamlit + docker services
  - start_all.cmd   -> easy double-click starter

Usage (PowerShell):
  cd "c:\Users\p venkata naveen\Cursor ai\Quant_Forex_V3\QUANT_ENGINE_2026\run-scripts"
  .\start_all.ps1

Usage (double-click):
  Run start_all.cmd

Notes:
  - Keep MT5 terminal open and logged in.
  - Dashboard URL: http://localhost:8501
