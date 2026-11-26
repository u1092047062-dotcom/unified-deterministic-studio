# =============================================================================
# build_desktop_app.ps1 - Baut die Tauri-Desktop-App (Stufe 12)
# Schritte: Frontend-Assets pruefen -> Rust Core kompilieren -> Tauri-Bundle/.exe
# Aufruf:   powershell -ExecutionPolicy Bypass -File scripts\build_desktop_app.ps1
# Optionen: -SkipBundle      nur `cargo build --release` (exe ohne Installer)
#           -SkipPythonCore  PyO3-Modul (maturin) ueberspringen
# =============================================================================
param(
    [switch]$SkipBundle,
    [switch]$SkipPythonCore
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root "venv\Scripts\python.exe"

function Step($message) {
    Write-Host ""
    Write-Host "=== $message ===" -ForegroundColor Cyan
}

# --- 1) Frontend-Assets pruefen ---------------------------------------------
Step "1/4 Frontend-Assets pruefen"
$frontend = Join-Path $Root "frontend\cad_viewer_v2.html"
if (-not (Test-Path $frontend)) { throw "Frontend fehlt: $frontend" }
$frontendSize = (Get-Item $frontend).Length
if ($frontendSize -lt 5000) { throw "Frontend unvollstaendig ($frontendSize Bytes)" }
Write-Host "OK: cad_viewer_v2.html ($frontendSize Bytes)"

# --- 2) Rust Core: reine Rust-Seite (ohne PyO3) pruefen ---------------------
Step "2/4 Rust Core pruefen (reine Rust-Seite, no default features)"
cargo check --manifest-path (Join-Path $Root "rust_core\Cargo.toml") --no-default-features
if ($LASTEXITCODE -ne 0) { throw "cargo check (rust_core) fehlgeschlagen" }
cargo test --manifest-path (Join-Path $Root "rust_core\Cargo.toml") --no-default-features --quiet
if ($LASTEXITCODE -ne 0) { throw "cargo test (rust_core) fehlgeschlagen" }
Write-Host "OK: astar-Kern compiliert und Tests gruen"

# --- 3) PyO3-Modul (Python-Integration) -------------------------------------
if (-not $SkipPythonCore) {
    Step "3/4 PyO3-Modul bauen (maturin develop --release)"
    if (-not (Test-Path $VenvPython)) { throw "venv nicht gefunden: $VenvPython" }
    $env:VIRTUAL_ENV = Join-Path $Root "venv"
    $env:PATH = "$($env:VIRTUAL_ENV)\Scripts;$env:PATH"
    Push-Location (Join-Path $Root "rust_core")
    try { & $VenvPython -m maturin develop --release }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "maturin develop fehlgeschlagen" }
} else {
    Step "3/4 PyO3-Modul uebersprungen (-SkipPythonCore)"
}

# --- 4) Tauri-App ------------------------------------------------------------
Step "4/4 Tauri-App bauen"
$srcTauri = Join-Path $Root "src-tauri"
$hasTauriCli = $false
cargo tauri --version 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $hasTauriCli = $true }

if (-not $SkipBundle -and $hasTauriCli) {
    Write-Host "cargo tauri build (NSIS-Bundle + exe)..."
    cargo tauri build --manifest-path (Join-Path $srcTauri "Cargo.toml")
    if ($LASTEXITCODE -ne 0) { throw "tauri build fehlgeschlagen" }
    $exe = Join-Path $srcTauri "target\release\eda-cad-studio.exe"
    Write-Host ""
    Write-Host "Bundle fertig: $exe" -ForegroundColor Green
    Write-Host "Installer:     $srcTauri\target\release\bundle\nsis\"
} else {
    if (-not $SkipBundle -and -not $hasTauriCli) {
        Write-Warning "tauri-cli nicht installiert - nur exe (kein Installer)."
        Write-Warning "Installer-Build: cargo install tauri-cli --version '^2'  (danach Skript ohne -SkipBundle)"
    }
    cargo build --release --manifest-path (Join-Path $srcTauri "Cargo.toml")
    if ($LASTEXITCODE -ne 0) { throw "cargo build fehlgeschlagen" }
    $exe = Join-Path $srcTauri "target\release\eda-cad-studio.exe"
    Write-Host ""
    Write-Host "Build fertig: $exe" -ForegroundColor Green
}

Write-Host ""
Write-Host "Desktop-App bereit. Start: $exe" -ForegroundColor Green
