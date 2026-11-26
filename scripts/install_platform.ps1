#requires -Version 5.1
<#
    install_platform.ps1
    Idempotentes Windows-11-Setup fuer Unified Deterministic Studio.
    - Legt eine dedizierte lokale .venv im Projektroot an.
    - Installiert platform_core, eda-multiagent-core und art-restoration-ai Abhaengigkeiten.
    - Prueft Rust/Cargo-Toolchain (ohne automatische Windows-Aufgaben oder Hintergrunddienste).
#>

[CmdletBinding()]
param(
    [string]$PythonExe = 'python',
    [switch]$SkipPipUpgrade
)

$ErrorActionPreference = 'Stop'
$PlatformRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $PlatformRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

Write-Host '=== Unified Deterministic Studio – Setup ===' -ForegroundColor Cyan
Write-Host "PlatformRoot: $PlatformRoot"

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    Write-Host "Erstelle dedizierte Python-Venv unter $VenvDir ..." -ForegroundColor Yellow
    & $PythonExe -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw 'Erstellen der .venv fehlgeschlagen.' }
}

if (-not $SkipPipUpgrade) {
    & $VenvPython -m pip install --upgrade pip
}

Write-Host 'Installiere platform_core (editable)...' -ForegroundColor Cyan
& $VenvPython -m pip install -e (Join-Path $PlatformRoot 'packages\platform_core')

Write-Host 'Installiere eda-multiagent-core Anforderungen...' -ForegroundColor Cyan
& $VenvPython -m pip install -r (Join-Path $PlatformRoot 'apps\eda-multiagent-core\requirements.txt')

Write-Host 'Installiere art-restoration-ai Anforderungen ([dev])...' -ForegroundColor Cyan
& $VenvPython -m pip install -e ((Join-Path $PlatformRoot 'apps\art-restoration-ai') + '[dev]')

$cargo = Get-Command cargo -ErrorAction SilentlyContinue
if ($cargo) {
    Write-Host ("Rust/Cargo erkannt: " + (& cargo --version)) -ForegroundColor Green
} else {
    Write-Warning 'Cargo nicht im PATH gefunden – Rust-Crates benoetigen eine lokale Rust-Installation.'
}

Write-Host "`nSetup abgeschlossen. Starte Verifikation mit:" -ForegroundColor Green
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\verify_unified_platform.ps1"
