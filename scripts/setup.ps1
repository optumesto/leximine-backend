# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# First-time setup for Leximine, both halves.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Uninstall
#
# Builds the Python environment, downloads the speech model, puts the backend
# shortcut on the desktop and installs the mod into Minecraft as its own
# launcher profile. Run once; after that the desktop shortcut is the only thing
# you need, and it starts in seconds because this did the waiting.

param(
    [switch]$Uninstall,
    [switch]$SkipMod,
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
# Where the mod half lives. Several candidates, because this script has two
# very different audiences:
#
#   ..\leximine          a source checkout, the two repos side by side
#   ..\lingua-craft-mod  the same, from before the rename
#   ..\mod               a downloaded release, backend\ and mod\ in one folder
#
# Without the release layout here, setup.cmd in a zip finds no mod and
# cheerfully installs half the product.
$parent = Split-Path $root -Parent
$modRepo = $null
foreach ($candidate in @("leximine", "leximine-mod", "lingua-craft-mod", "mod")) {
    $path = Join-Path $parent $candidate
    if (Test-Path $path) { $modRepo = $path; break }
}
if (-not $modRepo) { $modRepo = Join-Path $parent "leximine" }

function Write-Step($text) { Write-Host "  $text" -ForegroundColor Gray }
function Write-Ok($text) { Write-Host "  $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "  $text" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  Leximine - setup" -ForegroundColor Cyan
Write-Host "  -------------------" -ForegroundColor DarkGray

# --- 1. python, dependencies, model -----------------------------------
# First, and in setup rather than at launch. These are the long, failure-prone
# downloads; doing them here means the desktop shortcut is instant afterwards,
# and that a failure happens in the step the player is already watching rather
# than in one they think is just a launcher.
if (-not $Uninstall) {
    Write-Host ""
    Write-Host "  [1/3] Python environment and speech model" -ForegroundColor White
    & powershell -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $PSScriptRoot "ensure_env.ps1") -WithModel -NoWait
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Warn2 "The environment is not ready, so setup stopped here."
        Write-Step "The reason is above. Nothing else has been changed."
        if (-not $NoWait) { Read-Host "  Press Enter to close" }
        exit 1
    }
}

# --- 2. the icon ------------------------------------------------------
if (-not $Uninstall) {
    if (-not (Test-Path (Join-Path $root "leximine.ico"))) {
        $python = Join-Path $root ".venv\Scripts\python.exe"
        if (-not (Test-Path $python)) { $python = "python" }
        & $python (Join-Path $PSScriptRoot "make_icon.py") | Out-Null
    }
}

# --- 3. desktop shortcut ---------------------------------------------
Write-Host ""
Write-Host "  [2/3] Desktop shortcut" -ForegroundColor White
$shortcutArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass",
                  "-File", (Join-Path $PSScriptRoot "install_shortcut.ps1"))
if ($Uninstall) { $shortcutArgs += "-Remove" }
& powershell @shortcutArgs

# --- 4. the mod ------------------------------------------------------
Write-Host "  [3/3] Minecraft mod" -ForegroundColor White
if ($SkipMod) {
    Write-Step "Skipped (-SkipMod)."
} elseif (-not (Test-Path $modRepo)) {
    Write-Warn2 "Mod project not found next to this one at:"
    Write-Step "  $modRepo"
    Write-Step "Run install_mod.ps1 from wherever leximine lives."
} else {
    $modArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", (Join-Path $modRepo "scripts\install_mod.ps1"), "-NoWait")
    if ($Uninstall) { $modArgs += "-Uninstall" }
    & powershell @modArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "The mod step did not finish. The backend shortcut still works."
    }
}

# --- done -------------------------------------------------------------
Write-Host ""
if ($Uninstall) {
    Write-Ok "Uninstalled. Your worlds and this folder are untouched."
} else {
    Write-Host "  ======================================================" -ForegroundColor Green
    Write-Host "   Setup complete." -ForegroundColor Green
    Write-Host "  ======================================================" -ForegroundColor Green
    Write-Host ""
    Write-Step "From now on: double-click 'Leximine Backend' on your desktop,"
    Write-Step "wait for READY, then launch Minecraft's 'Leximine' profile."
}
Write-Host ""
if (-not $NoWait) { Read-Host "  Press Enter to close" }
