$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

Write-Host "=== QUANT ENGINE START ALL ===" -ForegroundColor Cyan
Write-Host "Project root: $root"

Write-Host "[1/4] Ensuring docker services are up (redis/postgres)..." -ForegroundColor Yellow
docker compose -f "docker/docker-compose.yml" up -d

Write-Host "[2/4] Stopping stale engine/dashboard python processes..." -ForegroundColor Yellow
$patterns = @("python run.py", "python simple_main.py", "python brain.py", "streamlit run dashboard\dashboard_app.py", "streamlit run dashboard_app.py")
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -and ($patterns | ForEach-Object { $_.CommandLine -like "*$_*" } | Where-Object { $_ } | Measure-Object).Count -gt 0
} | ForEach-Object {
    try {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  stopped PID $($_.ProcessId)"
    } catch {}
}

Start-Sleep -Seconds 1

Write-Host "[3/4] Starting engine (simple_main.py) in new terminal..." -ForegroundColor Yellow
Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit","-NoProfile","-Command","python simple_main.py"

Write-Host "[4/4] Starting dashboard (streamlit) in new terminal..." -ForegroundColor Yellow
Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit","-NoProfile","-Command","python -m streamlit run dashboard_app.py"

Write-Host ""
Write-Host "Started. Open: http://localhost:8501" -ForegroundColor Green
Write-Host "Tip: keep MT5 terminal open and logged in." -ForegroundColor Green
