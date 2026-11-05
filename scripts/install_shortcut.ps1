# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Put a "Leximine Backend" shortcut on the desktop.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1 -Remove
#
# Creates one .lnk in your own Desktop folder and nothing else -- no registry,
# no startup entries, no admin rights.

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$desktop = [Environment]::GetFolderPath("Desktop")
$linkPath = Join-Path $desktop "Leximine Backend.lnk"
# What this shortcut was called before the mod was renamed. Cleaned up on both
# paths, so nobody is left with two icons that start the same backend and no
# way to tell which one is current.
$legacyLink = Join-Path $desktop "LinguaCraft Backend.lnk"

if ($Remove) {
    $removed = $false
    foreach ($path in @($linkPath, $legacyLink)) {
        if (Test-Path $path) {
            Remove-Item $path -Force
            Write-Host "Removed $path" -ForegroundColor Yellow
            $removed = $true
        }
    }
    if (-not $removed) { Write-Host "No shortcut to remove." }
    exit 0
}

$target = Join-Path $root "start-backend.cmd"
$icon = Join-Path $root "leximine.ico"

if (-not (Test-Path $target)) { throw "missing $target" }
if (-not (Test-Path $icon)) {
    Write-Host "Icon missing; generating it..." -ForegroundColor Gray
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { $python = "python" }
    & $python (Join-Path $PSScriptRoot "make_icon.py")
}

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = $target
$link.WorkingDirectory = $root
$link.Description = "Start the Leximine speech backend, then play Minecraft"
if (Test-Path $icon) { $link.IconLocation = "$icon,0" }
$link.Save()

if (Test-Path $legacyLink) {
    Remove-Item $legacyLink -Force
    Write-Host "  Removed the old 'LinguaCraft Backend' shortcut." -ForegroundColor Gray
}

Write-Host ""
Write-Host "  Created: $linkPath" -ForegroundColor Green
Write-Host "  Double-click it to start the backend." -ForegroundColor Gray
Write-Host ""
Write-Host "  To pin it to the taskbar or Start, right-click the shortcut and" -ForegroundColor Gray
Write-Host "  choose Pin. To remove it, re-run this script with -Remove." -ForegroundColor Gray
Write-Host ""
