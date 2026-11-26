$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) { & py -3 -m venv .venv }
else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw 'Python 3.11+ wurde nicht gefunden. Bitte Python installieren und erneut starten.' }
    & python -m venv .venv
}

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e '.[dev]'
if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
Write-Host 'Installation abgeschlossen. Danach start_windows.ps1 ausführen.' -ForegroundColor Green
