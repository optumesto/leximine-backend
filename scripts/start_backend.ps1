# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# One-click start for the Leximine speech backend.
#
# Safe to double-click on a machine that has never run it: creates the venv,
# installs dependencies, extracts the Minecraft language file and downloads
# the speech model, each only if it is actually missing. On every run after
# the first it goes straight to listening.
#
# Reached through start-backend.cmd, which is what the desktop shortcut points
# at. Run directly with:
#     powershell -ExecutionPolicy Bypass -File scripts\start_backend.ps1

param(
    [switch]$NoWait,
    [string]$Language = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
$port = 8760

function Write-Step($text) { Write-Host "  $text" -ForegroundColor Gray }
function Write-Ok($text) { Write-Host "  $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "  $text" -ForegroundColor Yellow }
function Write-Bad($text) { Write-Host "  $text" -ForegroundColor Red }

function Stop-Here($code) {
    Write-Host ""
    if (-not $NoWait) { Read-Host "  Press Enter to close" }
    exit $code
}

function Test-PortOpen {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", $port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Get-BackendHealth {
    try {
        $response = Invoke-WebRequest "http://127.0.0.1:$port/health" `
            -UseBasicParsing -TimeoutSec 3
        return $response.Content | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Show-Summary($health) {
    Write-Step "language   $($health.target_lang)   ($($health.entries) words)"
    Write-Step "model      $($health.model) on $($health.device)"
    Write-Step "listening  ws://127.0.0.1:$port/ws"
    if ($health.device -ne "cuda") {
        Write-Warn2 "Running on CPU - answers will be slower than they should be."
    }
}

# Mentioned once, and only when it is actually actionable: a backend with no
# mod installed is a server nobody is going to talk to.
function Show-ModHint {
    $profiles = "$env:APPDATA\.minecraft\launcher_profiles.json"
    if (-not (Test-Path $profiles)) { return }
    try {
        $json = Get-Content $profiles -Raw | ConvertFrom-Json
        # Must match install_mod.ps1's -ProfileKey. It was "linguacraft" and
        # the rename deleted that profile without updating this check, so the
        # hint fired on every single start telling people to install something
        # they already had.
        if ($json.profiles.PSObject.Properties.Name -contains "leximine") { return }
    } catch {
        return
    }
    Write-Host ""
    Write-Warn2 "The Minecraft side is not installed yet."
    Write-Step "Run setup.cmd once to add the 'Leximine' profile to your launcher."
}

Write-Host ""
Write-Host "  Leximine - speech backend" -ForegroundColor Cyan
Write-Host "  -------------------------" -ForegroundColor DarkGray
Write-Host ""

# --- already running, or is something else on the port? ---------------
if (Test-PortOpen) {
    $health = Get-BackendHealth
    if ($health) {
        Write-Ok "Already running on port $port."
        Show-Summary $health
        Write-Host ""
        Write-Step "Start Minecraft and play - nothing else to do."
        Write-Step "To restart it: close the backend's window, or run stop-backend.cmd"
        Write-Step "if that window is gone and this keeps saying 'already running'."
        Show-ModHint
        Stop-Here 0
    }
    Write-Bad "Port $port is in use by another program."
    Write-Step "Close whatever is using it, or set LEXIMINE_PORT in .env to a free port"
    Write-Step "(and pass the same one to Minecraft with -Dleximine.backend)."
    Stop-Here 1
}

# --- python and dependencies ------------------------------------------
# Delegated to ensure_env.ps1, which setup.ps1 also runs. This is the safety
# net for anyone who skipped setup, or whose setup was interrupted: it checks
# each piece before fetching it, so when setup has already run this costs a
# second and prints two lines.
$firstRun = -not (Test-Path $python)
if ($firstRun) {
    Write-Step "First run: setting up. This takes a while, once."
    Write-Host ""
}
& powershell -NoProfile -ExecutionPolicy Bypass `
    -File (Join-Path $PSScriptRoot "ensure_env.ps1") -NoWait
if (($LASTEXITCODE -ne 0) -or (-not (Test-Path $python))) {
    Write-Bad "The Python environment is not ready. The reason is above."
    Stop-Here 1
}

# --- .env -------------------------------------------------------------
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Step "Created .env (edit it to change language, model or difficulty)."
}

foreach ($line in Get-Content ".env") {
    $trimmed = $line.Trim()
    if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
    $split = $trimmed.IndexOf("=")
    if ($split -lt 1) { continue }
    Set-Item -Path ("Env:" + $trimmed.Substring(0, $split).Trim()) `
             -Value $trimmed.Substring($split + 1).Trim().Trim('"')
}

if ($Language -ne "") { $env:LEXIMINE_TARGET_LANG = $Language }
# LINGUA_* is the pre-rename prefix, still honoured because .env files that
# predate 2026-11-05 use it -- as does the 0.1.0 release. The backend itself
# reads both; this script has to as well or it would report the wrong language.
$target = if ($env:LEXIMINE_TARGET_LANG) { $env:LEXIMINE_TARGET_LANG } else { $env:LINGUA_TARGET_LANG }
if (-not $target) { $target = "es_es" }

# --- dictionary -------------------------------------------------------
if (-not (Test-Path "lang\$target.json")) {
    Write-Step "Extracting the $target dictionary from your Minecraft install..."
    & $python scripts\extract_lang.py --langs $target --version 1.20.4
    if (-not (Test-Path "lang\$target.json")) {
        Write-Warn2 "Could not find $target in your Minecraft assets."
        Write-Step "Launch Minecraft once with that language selected, then run this again."
        Write-Step "Continuing with the small built-in word list for now."
    } else {
        Write-Ok "Dictionary ready."
    }
}

# --- go ---------------------------------------------------------------
$model = if ($env:LEXIMINE_MODEL) { $env:LEXIMINE_MODEL } else { $env:LINGUA_MODEL }
if (-not $model) { $model = "small" }
$downloading = -not (Test-Path "models")
if ($downloading) {
    Write-Step "First run also downloads the '$model' speech model. Please wait."
}

Write-Step "Starting (language $target, model $model)..."
Write-Host ""

$started = Get-Date
$server = Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", `
                  "--port", "$port", "--ws-max-size", "16777216" `
    -NoNewWindow -PassThru

# Wait on the socket rather than polling /health: uvicorn binds the port only
# after the model has finished loading, so the port opening *is* the ready
# signal, and this keeps a wall of "GET /health 200 OK" out of the log.
$deadline = (Get-Date).AddMinutes(20)
$ready = $false
while ((Get-Date) -lt $deadline) {
    if ($server.HasExited) { break }
    if (Test-PortOpen) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
}

Write-Host ""
if (-not $ready) {
    if ($server.HasExited) {
        Write-Bad "The backend stopped during startup. The log above says why."
    } else {
        Write-Bad "The backend did not finish starting."
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
    Stop-Here 1
}

$health = Get-BackendHealth
$elapsed = [math]::Round(((Get-Date) - $started).TotalSeconds)

Write-Host "  ======================================================" -ForegroundColor Green
Write-Host "   READY - start Minecraft and pick something up." -ForegroundColor Green
Write-Host "  ======================================================" -ForegroundColor Green
Write-Host ""
if ($health) {
    Show-Summary $health
} else {
    Write-Warn2 "Listening, but /health did not answer - check the log above."
}
Write-Step "ready in   ${elapsed}s"
Show-ModHint
Write-Host ""
Write-Step "Keep this window open while you play. Close it to stop the backend."
Write-Host ""

try {
    Wait-Process -Id $server.Id
} finally {
    if (-not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
