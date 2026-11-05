# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# First-time setup for Leximine, both halves.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Uninstall
#
# Puts the backend shortcut on the desktop and installs the mod into Minecraft
# as its own launcher profile. Run once; after that the desktop shortcut is the
# only thing you need.

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
#   ..\leximine-mod      a source checkout, the two repos side by side
#   ..\lingua-craft-mod  the same, from before the rename
#   ..\mod               a downloaded release, backend\ and mod\ in one folder
#
# Without the release layout here, setup.cmd in a zip finds no mod and
# cheerfully installs half the product.
$parent = Split-Path $root -Parent
$modRepo = $null
foreach ($candidate in @("leximine-mod", "lingua-craft-mod", "mod")) {
    $path = Join-Path $parent $candidate
    if (Test-Path $path) { $modRepo = $path; break }
}
if (-not $modRepo) { $modRepo = Join-Path $parent "leximine-mod" }

function Write-Step($text) { Write-Host "  $text" -ForegroundColor Gray }
function Write-Ok($text) { Write-Host "  $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "  $text" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  Leximine - setup" -ForegroundColor Cyan
Write-Host "  -------------------" -ForegroundColor DarkGray

# --- 1. the icon ------------------------------------------------------
if (-not $Uninstall) {
    if (-not (Test-Path (Join-Path $root "leximine.ico"))) {
        $python = Join-Path $root ".venv\Scripts\python.exe"
        if (-not (Test-Path $python)) { $python = "python" }
        & $python (Join-Path $PSScriptRoot "make_icon.py") | Out-Null
    }
}

# --- 2. desktop shortcut ---------------------------------------------
Write-Host ""
Write-Host "  [1/2] Desktop shortcut" -ForegroundColor White
$shortcutArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass",
                  "-File", (Join-Path $PSScriptRoot "install_shortcut.ps1"))
if ($Uninstall) { $shortcutArgs += "-Remove" }
& powershell @shortcutArgs

# --- 3. the mod ------------------------------------------------------
Write-Host "  [2/2] Minecraft mod" -ForegroundColor White
if ($SkipMod) {
    Write-Step "Skipped (-SkipMod)."
} elseif (-not (Test-Path $modRepo)) {
    Write-Warn2 "Mod project not found next to this one at:"
    Write-Step "  $modRepo"
    Write-Step "Run install_mod.ps1 from wherever leximine-mod lives."
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
