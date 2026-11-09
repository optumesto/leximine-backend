# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Make sure .venv\Scripts\python.exe exists and has the dependencies.
#
#   powershell -ExecutionPolicy Bypass -File scripts\ensure_env.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\ensure_env.ps1 -WithModel
#
# Called by setup.ps1, so the long waits happen in the step people expect to
# wait in, and by start_backend.ps1, which must still work for anyone who
# never ran setup.
#
# Everything here is idempotent: each piece is checked before it is fetched,
# so a second run costs a second.

param(
    [switch]$WithModel,
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$venv = Join-Path $root ".venv"
$python = Join-Path $venv "Scripts\python.exe"

# --- pinned, and checksummed, the way install_mod.ps1 pins Fabric API ------
#
# Python only has to be fetched when the machine has none. The embeddable
# distribution is the official zip from python.org: no installer, no registry,
# no PATH, nothing to uninstall -- deleting .venv removes it completely.
#
# The SHA-256 below was taken from the file this script downloads, and the
# same file's MD5 was checked against the one python.org publishes on the
# 3.11.9 release page (6d9aa08531d48fcc261ba667e2df17c4). Pin both halves
# together if you ever bump the version.
$PY_VERSION = "3.11.9"
$PY_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
$PY_SHA256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"

# The embeddable distribution ships without pip AND without ensurepip and
# venv, so pip cannot install itself and cannot be asked to. A wheel is a zip,
# and pip is pure Python, so unpacking the wheel into site-packages is exactly
# what pip would have done anyway.
$PIP_VERSION = "24.3.1"
$PIP_URL = "https://files.pythonhosted.org/packages/ef/7d/500c9ad20238fcfcb4cb9243eede163594d7020ce87bd9610c9e02771876/pip-24.3.1-py3-none-any.whl"
$PIP_SHA256 = "3790624780082365f47549d032f3770eeb2b1e8bd1f7b2e02dace1afa361b4ed"

# pip renders progress through `rich`, which writes box-drawing characters.
# On a console whose code page is 1252 that can die with a UnicodeEncodeError
# mid-install -- seen while testing this very script. Pinning the interpreter's
# IO encoding costs nothing and removes the whole class.
$env:PYTHONIOENCODING = "utf-8"

function Write-Step($text) { Write-Host "  $text" -ForegroundColor Gray }
function Write-Ok($text) { Write-Host "  $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "  $text" -ForegroundColor Yellow }
function Write-Bad($text) { Write-Host "  $text" -ForegroundColor Red }

function Stop-Here($code) {
    if (-not $NoWait) { Read-Host "  Press Enter to close" }
    exit $code
}

function Invoke-Quietly($exe, $arguments) {
    # PowerShell 5.1 turns a native command's stderr into an ErrorRecord, and
    # with $ErrorActionPreference = "Stop" that is terminating -- even with
    # 2>$null. So a probe whose whole purpose is to fail (an import check on a
    # fresh environment) would throw instead of returning a code. Drop the
    # preference for the length of the call and report the exit code.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $exe @arguments 2>$null | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    return $code
}

function Test-BackendImports {
    return (Invoke-Quietly $python @("-c", "import fastapi, faster_whisper, thefuzz, uvicorn")) -eq 0
}

function Get-Verified($url, $sha256, $destination) {
    Invoke-WebRequest -Uri $url -OutFile $destination -UseBasicParsing
    $actual = (Get-FileHash -Path $destination -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $sha256.ToLower()) {
        Remove-Item $destination -Force -ErrorAction SilentlyContinue
        Write-Bad "Checksum mismatch on $url"
        Write-Step "expected $sha256"
        Write-Step "got      $actual"
        Write-Step "Nothing was kept. Try again; if it repeats, report it."
        Stop-Here 1
    }
}

function Get-SystemPython {
    # Usable means 3.11 or newer. Anything older falls through to the
    # embeddable copy rather than erroring: a version that cannot run this is
    # not more helpful than no version at all.
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if (-not $candidate) { return $null }
    try { $raw = (& python --version 2>&1) } catch { return $null }
    if ($raw -notmatch 'Python (\d+)\.(\d+)') { return $null }
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    if ($major -lt 3) { return $null }
    if ($major -eq 3 -and $minor -lt 11) {
        Write-Warn2 "Found Python $major.$minor on PATH, but this needs 3.11 or newer."
        return $null
    }
    return $candidate.Source
}

function New-EmbeddedPython {
    # Laid out so that .venv\Scripts\python.exe is the interpreter, which is
    # the path every other script in this project already uses. Nothing else
    # has to know whether a real venv or this is underneath.
    Write-Step "No usable Python found. Fetching a private copy ($PY_VERSION, 11 MB)."
    Write-Step "It lives in .venv and touches nothing else on this machine."

    $scripts = Join-Path $venv "Scripts"
    $sitePackages = Join-Path $venv "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $scripts | Out-Null
    New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null

    $temp = Join-Path $env:TEMP "leximine-python-$PY_VERSION.zip"
    Get-Verified $PY_URL $PY_SHA256 $temp
    Expand-Archive -Path $temp -DestinationPath $scripts -Force
    Remove-Item $temp -Force -ErrorAction SilentlyContinue

    # The ._pth controls sys.path for an embeddable build. Two edits matter:
    # site-packages has to be on it, and `import site` has to be enabled or
    # pip is invisible even once it is installed. Paths here are relative to
    # python.exe.
    #
    # Written through WriteAllText with explicit ASCII rather than
    # Set-Content: PowerShell 5.1 would add a UTF-8 BOM, and Python reads the
    # BOM as part of the first path. That failure mode is silent -- the path
    # is simply never found.
    $pthLines = @("python311.zip", ".", "..\Lib\site-packages", "import site") -join "`r`n"
    [System.IO.File]::WriteAllText((Join-Path $scripts "python311._pth"),
                                   $pthLines + "`r`n",
                                   [System.Text.Encoding]::ASCII)

    Write-Step "Installing pip $PIP_VERSION..."
    # Saved as .zip, not .whl: Expand-Archive refuses any extension but .zip
    # ("...is not a supported archive file format"). A wheel is a zip, and
    # nothing here cares what the file is called -- only pip itself is fussy
    # about wheel filenames, and pip is not involved in unpacking this one.
    $wheel = Join-Path $env:TEMP "pip-$PIP_VERSION-py3-none-any.zip"
    Get-Verified $PIP_URL $PIP_SHA256 $wheel
    Expand-Archive -Path $wheel -DestinationPath $sitePackages -Force
    Remove-Item $wheel -Force -ErrorAction SilentlyContinue

    if ((Invoke-Quietly $python @("-m", "pip", "--version")) -ne 0) {
        Write-Bad "The private Python was fetched but pip did not come up."
        Write-Step "Delete the .venv folder and try again."
        Stop-Here 1
    }
    Write-Ok "Private Python $PY_VERSION ready."
}

# --- 1. an interpreter -----------------------------------------------------
if (Test-Path $python) {
    Write-Ok "Python environment already present."
} else {
    $systemPython = Get-SystemPython
    if ($systemPython) {
        Write-Step "Creating the virtual environment from $systemPython..."
        & python -m venv $venv
        if (($LASTEXITCODE -ne 0) -or (-not (Test-Path $python))) {
            Write-Warn2 "venv creation failed; falling back to a private copy."
            New-EmbeddedPython
        } else {
            Write-Ok "Virtual environment created."
        }
    } else {
        New-EmbeddedPython
    }
}

# --- 2. dependencies -------------------------------------------------------
# A cheap import check rather than running pip on every launch, which would
# add seconds to a start that should be instant.
if (-not (Test-BackendImports)) {
    Write-Host ""
    Write-Step "Installing dependencies. This is the long part of setup:"
    Write-Step "about 2.3 GB, several minutes, and longer on a slow connection."
    Write-Step "Leave the window alone -- closing it half way leaves a broken"
    Write-Step "install that has to be deleted by hand."
    Write-Host ""
    # Deliberately NOT --quiet. A silent window for ten minutes is where
    # people force-close, and a force-close during pip is the one failure that
    # needs manual cleanup. Progress bars are the whole point here.
    & $python -m pip install --disable-pip-version-check --no-warn-script-location --upgrade pip
    & $python -m pip install --disable-pip-version-check --no-warn-script-location -r (Join-Path $root "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Bad "Dependency install failed. The reason is above this line."
        Write-Step "A second run resumes rather than starting over."
        Stop-Here 1
    }
    # pip exiting 0 is not the same as the imports working: a resolver can
    # succeed while a wheel lands somewhere this interpreter does not look,
    # which is exactly the risk in an embeddable layout. Check the thing we
    # actually need rather than the thing pip reported.
    if (-not (Test-BackendImports)) {
        Write-Host ""
        Write-Bad "pip finished, but the backend's imports still fail."
        Write-Step "That usually means a package installed somewhere this"
        Write-Step "interpreter cannot see. Delete the .venv folder and re-run."
        Stop-Here 1
    }
    Write-Ok "Dependencies installed."
} else {
    Write-Ok "Dependencies already installed."
}

# --- 3. the speech model, optionally --------------------------------------
if ($WithModel) {
    Write-Host ""
    Write-Step "Fetching the speech model (461 MB, once)."
    & $python (Join-Path $PSScriptRoot "fetch_model.py")
    # Non-fatal on purpose: a missing model costs a wait on first launch,
    # where a failed setup costs the install. start_backend.ps1 retries.
}
