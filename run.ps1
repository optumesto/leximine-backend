# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Start the Lingua STT backend.
#
#   .\run.ps1              normal start
#   .\run.ps1 -Reload      auto-restart on code change (slow: reloads the model)
#
# Loads .env if present, then runs uvicorn from the project venv.

param([switch]$Reload)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "venv not found. Creating it..." -ForegroundColor Yellow
    python -m venv .venv
    & $python -m pip install --upgrade pip
    & $python -m pip install -r requirements.txt
}

$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Write-Host "loading .env" -ForegroundColor DarkGray
    foreach ($line in Get-Content $envFile) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) { continue }
        $name = $trimmed.Substring(0, $idx).Trim()
        $value = $trimmed.Substring($idx + 1).Trim().Trim('"')
        Set-Item -Path "Env:$name" -Value $value
    }
}

$extra = @()
if ($Reload) { $extra += "--reload" }

& $python -m uvicorn app.main:app `
    --host $(if ($env:LINGUA_HOST) { $env:LINGUA_HOST } else { "127.0.0.1" }) `
    --port $(if ($env:LINGUA_PORT) { $env:LINGUA_PORT } else { "8760" }) `
    --ws-max-size 16777216 `
    @extra
