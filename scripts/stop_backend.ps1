# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Stop the Leximine speech backend.
#
# Normally you just close the backend's window. This is for the case where
# that window was force-closed or lost: uvicorn can survive it, keep port 8760
# and keep the speech model resident in VRAM, after which the launcher reports
# "already running" for a backend you cannot see.

$ErrorActionPreference = "Continue"

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "uvicorn app\.main" }

if (-not $running) {
    Write-Host "  Nothing to stop - the backend is not running." -ForegroundColor Gray
    exit 0
}

foreach ($process in $running) {
    Write-Host "  Stopping backend (pid $($process.ProcessId))..." -ForegroundColor Yellow
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2
try {
    Invoke-WebRequest "http://127.0.0.1:8760/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
    Write-Host "  Something is still answering on port 8760." -ForegroundColor Red
} catch {
    Write-Host "  Stopped." -ForegroundColor Green
}
