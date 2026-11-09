# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Leximine preflight: check every piece and say exactly what is missing.
#
#   powershell -ExecutionPolicy Bypass -File scripts\doctor.ps1
#
# Reads only. Nothing here installs, downloads, starts or changes anything --
# the whole point is that it is safe to run when you have no idea what state
# you are in.
#
# Exit code 0 if the required pieces are all there, 1 if any of them is not.
# Optional pieces (the language model behind villagers and the nether portal)
# warn but never fail: the mod is designed to let you through when they are
# missing, so a machine without them is degraded, not broken.

param([switch]$NoWait)

$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent

$script:Failures = 0
$script:Warnings = 0

function Section($text) {
    Write-Host ""
    Write-Host "  $text" -ForegroundColor White
}
function Ok($text) { Write-Host "    [ok]   $text" -ForegroundColor Green }
function Warn($text, $fix) {
    Write-Host "    [warn] $text" -ForegroundColor Yellow
    if ($fix) { Write-Host "           -> $fix" -ForegroundColor DarkGray }
    $script:Warnings++
}
function Bad($text, $fix) {
    Write-Host "    [FAIL] $text" -ForegroundColor Red
    if ($fix) { Write-Host "           -> $fix" -ForegroundColor DarkGray }
    $script:Failures++
}

function Get-Setting($name, $fallback) {
    # .env wins over the default, the same precedence the backend itself uses.
    # Reading it here rather than importing the app keeps this script usable
    # when the virtualenv is the thing that is broken.
    # LEXIMINE_<name> first, then the pre-rename LINGUA_<name> -- which is
    # what the 0.1.0 release shipped, so an existing .env still holds it.
    $envFile = Join-Path $root ".env"
    if (Test-Path $envFile) {
        foreach ($prefix in @("LEXIMINE_", "LINGUA_")) {
            $key = $prefix + $name
            foreach ($line in Get-Content $envFile) {
                if ($line -match "^\s*$key\s*=\s*(.+?)\s*$") { return $Matches[1] }
            }
        }
    }
    return $fallback
}

Write-Host ""
Write-Host "  Leximine - preflight" -ForegroundColor Cyan
Write-Host "  --------------------" -ForegroundColor DarkGray

# ----------------------------------------------------------------------
Section "Minecraft"
$mcRoot = Join-Path $env:APPDATA ".minecraft"
$profilesPath = Join-Path $mcRoot "launcher_profiles.json"
$gameDir = $null

if (-not (Test-Path $mcRoot)) {
    Bad "No Minecraft install at $mcRoot" "Install Minecraft Java Edition and run the launcher once."
} else {
    Ok "Minecraft found at $mcRoot"

    $fabric = Get-ChildItem (Join-Path $mcRoot "versions") -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "fabric-loader-*-1.20.4" }
    if ($fabric) {
        Ok "Fabric loader for 1.20.4 ($($fabric[0].Name))"
    } else {
        Bad "No Fabric loader for Minecraft 1.20.4" `
            "Get it from https://fabricmc.net/use/installer - pick 1.20.4."
    }

    if (-not (Test-Path $profilesPath)) {
        Bad "launcher_profiles.json not found" "Run the Minecraft launcher once, then re-run setup."
    } else {
        try {
            $json = Get-Content $profilesPath -Raw | ConvertFrom-Json
            $entry = $json.profiles.PSObject.Properties | Where-Object { $_.Name -eq "leximine" }
            if ($entry) {
                Ok "'Leximine' launcher profile present"
                $gameDir = $entry.Value.gameDir
            } else {
                Bad "No 'Leximine' profile in the launcher" "Run setup.cmd in this folder."
            }
        } catch {
            Bad "launcher_profiles.json could not be read" "The launcher rewrites it on exit; close the launcher and retry."
        }
    }
}

if (-not $gameDir) { $gameDir = Join-Path $mcRoot "leximine" }
$modsDir = Join-Path $gameDir "mods"
if (-not (Test-Path $modsDir)) {
    Bad "No mods folder at $modsDir" "Run setup.cmd in this folder."
} else {
    $mod = Get-ChildItem $modsDir -Filter "leximine-*.jar" -ErrorAction SilentlyContinue
    if ($mod) {
        Ok "$($mod[0].Name) installed"
    } else {
        Bad "The Leximine mod is not in $modsDir" "Run setup.cmd in this folder."
    }
    # Fabric API is a separate download and forgetting it is the single most
    # common way a Fabric mod "does nothing" -- the game starts and the mod
    # never loads, with the reason buried in the log.
    $api = Get-ChildItem $modsDir -Filter "fabric-api-*.jar" -ErrorAction SilentlyContinue
    if ($api) {
        Ok "Fabric API present ($($api[0].Name))"
    } else {
        Bad "Fabric API is missing" `
            "Download the 1.20.4 build from https://modrinth.com/mod/fabric-api and put the .jar in $modsDir"
    }
}

# ----------------------------------------------------------------------
Section "Speech backend (required)"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $pyVersion = (& $venvPython --version)
    Ok "virtualenv built ($pyVersion)"
} else {
    # Both routes build it, so name the one the player already has open.
    # Python no longer has to be installed first: ensure_env.ps1 fetches a
    # private copy when the machine has none, so "install Python" is advice
    # about speed, not a requirement.
    Bad "No virtualenv in this folder" `
        "Double-click Setup.cmd at the top of the Leximine folder. It builds .venv and installs the dependencies (about 2.3 GB, once)."
}

$modelSize = Get-Setting "MODEL" "small"
$modelDir = Join-Path $root "models"
$cached = Get-ChildItem $modelDir -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "models--Systran--faster-whisper-$modelSize" }
if (-not $cached) {
    Warn "Whisper model '$modelSize' is not downloaded yet" `
         "It downloads itself the first time the backend starts. Needs a few hundred MB and a few minutes."
} else {
    # Resolve model.bin rather than measuring the folder. HuggingFace stores
    # the weights once in a content-addressed blob store and links to them,
    # so a plain recursive size reports a few MB of tokenizer and says
    # nothing about whether the 460 MB of weights are actually there.
    #
    # Worth checking properly because of how it fails: Windows zip tools do
    # not preserve symlinks, so a models folder that was copied or unzipped
    # arrives looking complete with every weight file 0 bytes long. The
    # backend then dies on load with something about a corrupt model.
    $bin = Get-ChildItem $cached[0].FullName -Recurse -File -Filter "model.bin" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $bytes = 0
    if ($bin) {
        # Opened rather than stat'ed, because Windows follows symlinks on open
        # and this chain is two hops (snapshot -> blob -> content-addressed
        # blob) with relative targets. Reading .Target and resolving it by
        # hand got this wrong twice: a relative target resolves against the
        # working directory, not against the link, so a perfectly good model
        # was reported as empty. A broken link throws here, which is the case
        # worth catching anyway.
        try {
            $stream = [System.IO.File]::OpenRead($bin.FullName)
            $bytes = $stream.Length
            $stream.Close()
        } catch {
            $bytes = 0
        }
    }
    if ($bytes -gt 1MB) {
        Ok "Whisper model '$modelSize' downloaded ($([math]::Round($bytes / 1MB)) MB)"
    } elseif ($bin) {
        Bad "Whisper model '$modelSize' is present but empty" `
            "Delete the models folder and start the backend; it will download a clean copy. (Copying or unzipping this folder breaks the links to the weights.)"
    } else {
        Warn "Whisper model '$modelSize' looks incomplete" `
             "Delete the models folder and start the backend to re-download it."
    }
}

# A GPU that enumerates is not a GPU that decodes, so this only reports what
# is present; /health below is what actually proves CUDA works.
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $gpu = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
    if ($gpu) { Ok "NVIDIA GPU: $($gpu -join '; ')" }
} else {
    Warn "No NVIDIA GPU detected" `
         "It will run on the CPU, several times slower. Playable, but answers lag."
}

$port = Get-Setting "PORT" "8760"
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 5
    if (-not $health.ok) {
        # It answers /health while the model is still loading, which is the
        # state you are in if you start the backend and launch Minecraft
        # straight after. Everything is free until it finishes.
        Warn "backend is up on port $port but still loading its model" `
             "Wait for READY in the backend window before you start playing."
    } elseif ($health.device -eq "cuda") {
        Ok "backend answering on port $port (cuda, $($health.compute_type), $($health.entries) words)"
    } else {
        Warn "backend answering on port $port but running on $($health.device)" `
             "Check the backend window for a CUDA error; on CPU every answer is slow."
    }
} catch {
    Warn "backend is not running" "Start it with the 'Leximine Backend' desktop shortcut before you play."
}

# ----------------------------------------------------------------------
Section "Villagers and the nether portal (optional)"
$llmModel = Get-Setting "LLM_MODEL" "gemma3:12b"
try {
    $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    $names = @($tags.models | ForEach-Object { $_.name })
    if ($names -contains $llmModel) {
        Ok "Ollama running, judge model '$llmModel' pulled"
    } else {
        Warn "Ollama is running but '$llmModel' is not pulled" `
             "Run: ollama pull $llmModel   (about 8 GB)"
    }
} catch {
    Warn "Ollama is not running" `
         "Only villagers and the nether portal need it, and both let you through without it. Get it at https://ollama.com"
}

# ----------------------------------------------------------------------
Write-Host ""
if ($script:Failures -gt 0) {
    Write-Host "  $($script:Failures) thing(s) must be fixed before this will work." -ForegroundColor Red
    if ($script:Warnings -gt 0) { Write-Host "  $($script:Warnings) warning(s) as well." -ForegroundColor Yellow }
} elseif ($script:Warnings -gt 0) {
    Write-Host "  Ready to play, with $($script:Warnings) thing(s) worth knowing about." -ForegroundColor Yellow
} else {
    Write-Host "  Everything checks out." -ForegroundColor Green
    Write-Host "  Start the backend, wait for READY, then launch the Leximine profile." -ForegroundColor Gray
}
Write-Host ""

if (-not $NoWait) { Read-Host "  Press Enter to close" | Out-Null }
exit $(if ($script:Failures -gt 0) { 1 } else { 0 })
