# Pulls the latest boat-dashboard code and (re)starts the server, listening on every network
# interface (0.0.0.0) so it's reachable from another device on the same WiFi -- an iPad, say.
# Run from VS Code's terminal: .\run.ps1

Set-Location $PSScriptRoot

Write-Host "Pulling latest code..." -ForegroundColor Cyan
git pull
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull failed -- fix that before restarting the server." -ForegroundColor Red
    exit 1
}

$existing = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Stopping the server already running on port 8090..." -ForegroundColor Yellow
    $existing.OwningProcess | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceAlias -notmatch "Loopback" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1).IPAddress
Write-Host "Starting the dashboard -- open http://$($ip):8090 on your iPad (same WiFi as this PC)" -ForegroundColor Cyan

& "venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir . --host 0.0.0.0 --port 8090
