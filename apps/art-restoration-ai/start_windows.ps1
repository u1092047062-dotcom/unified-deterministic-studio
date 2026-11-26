$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Virtuelle Umgebung fehlt. Zuerst install_windows.ps1 ausführen.' }
$env:DATA_DIR = if ($env:DATA_DIR) { $env:DATA_DIR } else { Join-Path $root 'data' }
$env:WORK_DIR = if ($env:WORK_DIR) { $env:WORK_DIR } else { Join-Path $root 'work' }
New-Item -ItemType Directory -Force -Path $env:DATA_DIR, $env:WORK_DIR | Out-Null
Write-Host 'ART Restoration Engine 1.0 startet lokal auf http://127.0.0.1:8000' -ForegroundColor Cyan
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
