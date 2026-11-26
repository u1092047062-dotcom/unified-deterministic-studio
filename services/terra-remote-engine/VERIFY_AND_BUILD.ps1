#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$SkipClippy
)

# Fehler sichtbar halten, auch wenn das Skript per Doppelklick gestartet wird.
trap {
    $message = $_.Exception.Message
    Write-Host "`nFEHLER: $message" -ForegroundColor Red
    if ($ReportFile) {
        try { Add-Content -LiteralPath $ReportFile -Value "`r`nFEHLER: $message`r`n$($_ | Out-String)" -Encoding UTF8 } catch {}
        Write-Host "Pruefbericht: $ReportFile" -ForegroundColor Yellow
    }
    Write-Host "`nDas Fenster bleibt offen. Bitte Fehlermeldung fotografieren oder kopieren." -ForegroundColor Yellow
    Read-Host 'Enter zum Beenden'
    break
}
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ProjectRoot = $PSScriptRoot
$ReportRoot = Join-Path $ProjectRoot 'verification'
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$ReportFile = Join-Path $ReportRoot "TerraRemoteEngine_1_2_Verification_$Stamp.txt"
New-Item -ItemType Directory -Path $ReportRoot -Force | Out-Null
Set-Location -LiteralPath $ProjectRoot

function Invoke-Check {
    param([string]$Name,[scriptblock]$Command)
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    Add-Content -LiteralPath $ReportFile -Value "`r`n=== $Name ===" -Encoding UTF8
    & $Command 2>&1 | Tee-Object -FilePath $ReportFile -Append
    if ($LASTEXITCODE -ne 0) { throw "$Name fehlgeschlagen (Exitcode $LASTEXITCODE)." }
}

@("TerraRemoteEngine 1.2.0 – Build- und Prüfbericht", "Zeit: $(Get-Date -Format o)", "Projekt: $ProjectRoot", "Originale wurden durch dieses Skript nicht verändert.") | Set-Content -LiteralPath $ReportFile -Encoding UTF8

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw 'Cargo fehlt. Rust stable installieren und PowerShell neu öffnen.' }
if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) { throw 'rustc fehlt. Rust stable installieren und PowerShell neu öffnen.' }
Invoke-Check 'Rust-Versionen' { cargo --version; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; rustc --version }
Invoke-Check 'Formatprüfung (nur lesend)' { cargo fmt --all -- --check }
Invoke-Check 'Tests' { cargo test --all-targets --locked }
if (-not $SkipClippy) { Invoke-Check 'Clippy' { cargo clippy --all-targets --locked -- -D warnings } }
Invoke-Check 'Release-Build' { cargo build --release --locked }

$Exe = Join-Path $ProjectRoot 'target\release\rust-pc-remote.exe'
if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { throw "Build-EXE fehlt: $Exe" }
$Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
$Info = [Diagnostics.FileVersionInfo]::GetVersionInfo($Exe)
@("", "BUILD-ERGEBNIS: ERFOLGREICH", "EXE: $Exe", "Bytes: $((Get-Item -LiteralPath $Exe).Length)", "SHA256: $Hash", "Dateiversion: $($Info.FileVersion)", "Produktversion: $($Info.ProductVersion)", "Zeit: $(Get-Date -Format o)", "", "HINWEIS: Ein erfolgreicher Build beweist noch keine Laufzeitfunktion auf dem Ziel-PC.", "Windows-Aufgabe wurde nicht geändert oder gestartet.") | Add-Content -LiteralPath $ReportFile -Encoding UTF8
Write-Host "`nBUILD UND PRÜFUNGEN ERFOLGREICH" -ForegroundColor Green
Write-Host "EXE: $Exe"
Write-Host "SHA256: $Hash"
Write-Host "Bericht: $ReportFile"

Write-Host '`nPruefung beendet. Das Fenster bleibt bis Enter geoeffnet.' -ForegroundColor Cyan
Read-Host 'Enter zum Schliessen'
