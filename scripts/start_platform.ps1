#requires -Version 5.1
<#
    start_platform.ps1
    Startet Unified Deterministic Studio ausschliesslich lokal auf 127.0.0.1.
    -Mode gateway (Default): http://127.0.0.1:8080 (Cockpit + /eda + /art)
    -Mode eda:               http://127.0.0.1:8000 (nur EDA Core)
    -Mode art:               http://127.0.0.1:8010 (nur ART Restoration AI)
#>

[CmdletBinding()]
param(
    [ValidateSet('gateway', 'eda', 'art')][string]$Mode = 'gateway',
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$PlatformRoot = Split-Path -Parent $PSScriptRoot

$candidates = @(
    (Join-Path $PlatformRoot '.venv\Scripts\python.exe'),
    'D:\MEINE_PROJEKTE\.PMM_STAGING_20261121_124709\05_PLATINEN_ELEKTRONIK\eda-multiagent-core\venv\Scripts\python.exe'
)
$py = ($candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1)
if (-not $py) { $py = 'python' }

$extraSite = Join-Path $PlatformRoot '.venv\site-packages'
$pyPaths = @(
    (Join-Path $PlatformRoot 'packages\platform_core'),
    (Join-Path $PlatformRoot 'apps\eda-multiagent-core'),
    (Join-Path $PlatformRoot 'apps\art-restoration-ai')
)
if (Test-Path -LiteralPath $extraSite -PathType Container) { $pyPaths += $extraSite }
$env:PYTHONPATH = ($pyPaths -join ';')

switch ($Mode) {
    'gateway' {
        $p = if ($Port -gt 0) { $Port } else { 8080 }
        Write-Host "Starte Unified Platform Gateway auf http://127.0.0.1:$p ..." -ForegroundColor Cyan
        Set-Location (Join-Path $PlatformRoot 'apps\platform_gateway')
        & $py -m uvicorn main:gateway --host 127.0.0.1 --port $p
    }
    'eda' {
        $p = if ($Port -gt 0) { $Port } else { 8000 }
        Write-Host "Starte EDA Multi-Agent Core auf http://127.0.0.1:$p ..." -ForegroundColor Cyan
        Set-Location (Join-Path $PlatformRoot 'apps\eda-multiagent-core')
        & $py -m uvicorn server.server:app --host 127.0.0.1 --port $p
    }
    'art' {
        $p = if ($Port -gt 0) { $Port } else { 8010 }
        Write-Host "Starte ART Restoration AI auf http://127.0.0.1:$p ..." -ForegroundColor Cyan
        Set-Location (Join-Path $PlatformRoot 'apps\art-restoration-ai')
        & $py -m uvicorn app.main:app --host 127.0.0.1 --port $p
    }
}
