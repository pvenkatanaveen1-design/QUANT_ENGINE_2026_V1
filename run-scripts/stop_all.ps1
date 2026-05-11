$ErrorActionPreference = "Continue"

Write-Host "=== QUANT ENGINE STOP ALL ===" -ForegroundColor Cyan

Write-Host "[1/3] Stopping engine/dashboard python processes..." -ForegroundColor Yellow
$patterns = @("python run.py", "python simple_main.py", "python brain.py", "streamlit run dashboard\dashboard_app.py", "streamlit run dashboard_app.py")
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -and ($patterns | ForEach-Object { $_.CommandLine -like "*$_*" } | Where-Object { $_ } | Measure-Object).Count -gt 0
} | ForEach-Object {
    try {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  stopped PID $($_.ProcessId)"
    } catch {}
}

Write-Host "[2/3] Stopping docker services..." -ForegroundColor Yellow
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
docker compose -f "docker/docker-compose.yml" down

Write-Host "[3/3] Done." -ForegroundColor Green
