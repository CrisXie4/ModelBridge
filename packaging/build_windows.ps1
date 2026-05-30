# ============================================================================
#  ModelBridge - Windows build script (PowerShell)
# ----------------------------------------------------------------------------
#  Runs PyInstaller to produce dist\mbridge\mbridge.exe, then runs Inno
#  Setup to produce packaging\Output\ModelBridge-Setup-x.y.z.exe.
#
#  Usage:
#      powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#  Or override the Python install used:
#      $env:MBRIDGE_PYTHON = "C:\Python311\python.exe"
#      powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# ============================================================================

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)   # repo root

# ---------------------------------------------------------------------------
# Resolve Inno Setup ISCC.exe. Order:
#   1. $env:ISCC_PATH  (full path override)
#   2. Common install dirs: C:\Program Files (x86)\..., C:\Program Files\...,
#      D:\..., E:\..., F:\... (some users install to a non-default drive)
#   3. ISCC on PATH (some installers add it, some don't)
# Compil32.exe is the IDE; the headless compiler is ISCC.exe in the same dir.
# ---------------------------------------------------------------------------
function Find-ISCC {
    if ($env:ISCC_PATH -and (Test-Path $env:ISCC_PATH)) {
        return $env:ISCC_PATH
    }
    $candidates = @()
    foreach ($drive in @("C:", "D:", "E:", "F:")) {
        $candidates += "$drive\Program Files (x86)\Inno Setup 6\ISCC.exe"
        $candidates += "$drive\Program Files\Inno Setup 6\ISCC.exe"
        $candidates += "$drive\Inno Setup 6\ISCC.exe"
        $candidates += "$drive\Program Files (x86)\Inno Setup 5\ISCC.exe"
        $candidates += "$drive\Program Files\Inno Setup 5\ISCC.exe"
    }
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    $cmd = Get-Command ISCC -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$IsccPath = Find-ISCC

# ---------------------------------------------------------------------------
# Resolve a Python that has pip working.
# ---------------------------------------------------------------------------
function Test-Python([string[]]$Argv) {
    try {
        & $Argv[0] @($Argv[1..($Argv.Length-1)] + @("-m", "pip", "--version")) *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

$Python = $null

# 1. Explicit override
if ($env:MBRIDGE_PYTHON) {
    if (Test-Python @($env:MBRIDGE_PYTHON)) {
        $Python = @($env:MBRIDGE_PYTHON)
    } else {
        Write-Error "MBRIDGE_PYTHON=$($env:MBRIDGE_PYTHON) has no working pip"
        exit 1
    }
}

# 2. py launcher: prefer validated versions
if (-not $Python -and (Get-Command py -ErrorAction SilentlyContinue)) {
    foreach ($v in @("3.11", "3.12", "3.13", "3.10", "3.14")) {
        if (Test-Python @("py", "-$v")) {
            $Python = @("py", "-$v")
            break
        }
    }
    if (-not $Python -and (Test-Python @("py", "-3"))) {
        $Python = @("py", "-3")
    }
}

# 3. python on PATH (only if it has pip)
if (-not $Python -and (Get-Command python -ErrorAction SilentlyContinue)) {
    if (Test-Python @("python")) {
        $Python = @("python")
    }
}

# 4. Common python.org install paths
if (-not $Python) {
    foreach ($v in @("Python313", "Python312", "Python311", "Python310")) {
        $candidate = "$env:LOCALAPPDATA\Programs\Python\$v\python.exe"
        if (Test-Path $candidate) {
            if (Test-Python @($candidate)) {
                $Python = @($candidate)
                break
            }
        }
    }
}

if (-not $Python) {
    Write-Host ""
    Write-Host "ERROR: could not find a Python install with pip." -ForegroundColor Red
    Write-Host "Tried: MBRIDGE_PYTHON env var, py launcher, python on PATH, %LOCALAPPDATA%\Programs\Python\..."
    Write-Host ""
    Write-Host "Fixes:"
    Write-Host "  - Install Python 3.10+ from https://www.python.org/downloads/"
    Write-Host "    (tick 'Add python.exe to PATH' and 'Install pip')"
    Write-Host "  - Or set MBRIDGE_PYTHON to a full python.exe path with pip:"
    Write-Host '      $env:MBRIDGE_PYTHON = "C:\Python311\python.exe"'
    Write-Host "      powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1"
    exit 1
}

Write-Host "Using Python: $($Python -join ' ')" -ForegroundColor Cyan
& $Python[0] @($Python[1..($Python.Length-1)] + @("--version"))
Write-Host ""

function Invoke-Py([string[]]$PyArgs) {
    & $Python[0] @($Python[1..($Python.Length-1)] + $PyArgs)
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Command failed: $($Python -join ' ') $($PyArgs -join ' ')"
        exit 1
    }
}

# ---------------------------------------------------------------------------
# [1/3] Install build deps.
#
# We DO NOT run ``pip install -e .`` — that's a dev-environment concern.
# The PyInstaller spec puts the repo root on sys.path via pathex, so the
# frozen build doesn't need modelbridge to be importable from anywhere
# else. Skipping the editable install also dodges WinError 32 when an
# old mbridge.exe in Scripts\ is still locked by a running process.
# ---------------------------------------------------------------------------
Write-Host "=== [1/3] Installing build deps (pyinstaller + runtime deps) ===" -ForegroundColor Cyan
Invoke-Py @("-m", "pip", "install", "--upgrade", "pip", "pyinstaller")
# Project runtime deps must be importable by the chosen Python so
# PyInstaller can trace them. ``pip install -r`` would need a
# requirements file; reading pyproject.toml's [project.dependencies] is
# cleaner. typer / pydantic / etc. are explicit.
Invoke-Py @("-m", "pip", "install", "--upgrade",
            "typer>=0.12.0", "pydantic>=2.6.0", "httpx>=0.27.0",
            "PyYAML>=6.0", "rich>=13.7.0")

# ---------------------------------------------------------------------------
# [2/3] PyInstaller.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== [2/3] Running PyInstaller ===" -ForegroundColor Cyan
Invoke-Py @("-m", "PyInstaller", "packaging\mbridge.spec", "--clean", "--noconfirm")

if (-not (Test-Path "dist\mbridge\mbridge.exe")) {
    Write-Error "PyInstaller did not produce dist\mbridge\mbridge.exe"
    exit 1
}

Write-Host "Quick smoke test:" -ForegroundColor Cyan
& "dist\mbridge\mbridge.exe" version
if ($LASTEXITCODE -ne 0) {
    Write-Error "Frozen mbridge.exe failed its smoke test."
    exit 1
}

# ---------------------------------------------------------------------------
# [3/3] Inno Setup -> installer.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== [3/3] Building installer with Inno Setup ===" -ForegroundColor Cyan
if (-not $IsccPath) {
    Write-Host ""
    Write-Host "Inno Setup (ISCC.exe) not found." -ForegroundColor Yellow
    Write-Host "Tried: `$env:ISCC_PATH, C:\D:\E:\F:\Program Files... \Inno Setup 6\, ISCC on PATH."
    Write-Host "Download from https://jrsoftware.org/isdl.php and install,"
    Write-Host "or set ISCC_PATH to the full path of ISCC.exe (NOT Compil32.exe):"
    Write-Host '   $env:ISCC_PATH = "E:\Inno Setup 6\ISCC.exe"'
    Write-Host ""
    Write-Host "Skipped installer build. dist\mbridge\ is ready as a portable build -"
    Write-Host "you can zip it and send it as-is; users just run mbridge.exe inside." -ForegroundColor Green
    exit 0
}

Write-Host "Using ISCC: $IsccPath" -ForegroundColor Cyan
& $IsccPath "packaging\installer.iss"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Inno Setup failed."
    exit 1
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
Write-Host "Installer: packaging\Output\ModelBridge-Setup-*.exe"
