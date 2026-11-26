#requires -Version 5.1
<#
    collect_crash_report.ps1
    SAFE READ-ONLY CRASH DIAGNOSTIC (parametrisierte Fassung von Terra_Crash_Analyse.ps1):
    - Prueft Metadaten/SHA-256 der angegebenen Executables (Standard: eda-cad-studio.exe, rust-pc-remote.exe).
    - Liest Windows Error Reporting (WER) Archiv-Eintraege und .dmp-Metadaten (nur lesend).
    - Liest Windows Application Event Log IDs 1000/1001 fuer die Ziel-Executables.
    - Veraendert keine Dateien im Projekt und startet keine Programme.
#>

[CmdletBinding()]
param(
    [string[]]$ExecutableNames = @('eda-cad-studio.exe', 'rust-pc-remote.exe'),
    [string]$ProjectRoot = 'D:\MEINE_PROJEKTE\.PMM_STAGING_20261121_124709\05_PLATINEN_ELEKTRONIK',
    [string]$CargoTargetDir = 'D:\MEINE_PROJEKTE\.cargo_target_cache',
    [int]$LookbackDays = 7,
    [string]$ReportFile = ''
)

$ErrorActionPreference = 'Continue'
$EdaRoot = Split-Path -Parent $PSScriptRoot
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'

if ([string]::IsNullOrWhiteSpace($ReportFile)) {
    $verifyDir = Join-Path $EdaRoot 'verification'
    New-Item -ItemType Directory -Path $verifyDir -Force | Out-Null
    $ReportFile = Join-Path $verifyDir "eda_crash_report_$Stamp.txt"
}
else {
    $parent = Split-Path -Parent $ReportFile
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

$WER = Join-Path $env:ProgramData 'Microsoft\Windows\WER'
$lines = New-Object System.Collections.Generic.List[string]

function Add-Line {
    param([string]$Text = '')
    $lines.Add($Text)
    Write-Host $Text
}

Add-Line '=== EDA CRASH REPORT (READ-ONLY) ==='
Add-Line "Zeit:            $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Line "ProjectRoot:     $ProjectRoot"
Add-Line "CargoTargetDir:  $CargoTargetDir"
Add-Line "Executables:     $($ExecutableNames -join ', ')"
Add-Line "LookbackDays:    $LookbackDays"

# 1) Binaries pruefen
Add-Line ''
Add-Line '=== BINARIES (SHA-256) ==='
$candidateRoots = @(
    (Join-Path $CargoTargetDir 'release'),
    (Join-Path $EdaRoot 'src-tauri\target\release'),
    (Join-Path $ProjectRoot 'tools\monitoring\terra-remote-engine\target\release')
)

foreach ($exeName in $ExecutableNames) {
    $foundAny = $false
    foreach ($dir in $candidateRoots) {
        $exePath = Join-Path $dir $exeName
        if (Test-Path -LiteralPath $exePath -PathType Leaf) {
            $foundAny = $true
            $item = Get-Item -LiteralPath $exePath
            $hash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash
            Add-Line "FOUND: $exePath | $($item.Length) Bytes | LastWrite: $($item.LastWriteTime) | SHA256: $hash"
        }
    }
    if (-not $foundAny) {
        Add-Line "INFO: $exeName nicht in den Standard-Release-Pfaden gefunden."
    }
}

# 2) WER Crash Reports & Dumps
$baseNames = @($ExecutableNames | ForEach-Object { [System.IO.Path]::GetFileNameWithoutExtension($_) })
$regexPattern = ($baseNames | ForEach-Object { [regex]::Escape($_) }) -join '|'

Add-Line ''
Add-Line '=== WINDOWS ERROR REPORTING (WER) ==='
$reports = @(Get-ChildItem -LiteralPath (Join-Path $WER 'ReportArchive') -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match $regexPattern } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 20)

if ($reports.Count -gt 0) {
    foreach ($r in $reports) {
        Add-Line "WER-Report: $($r.FullName) | $($r.LastWriteTime)"
    }
}
else {
    Add-Line 'Keine passenden WER-ReportArchive-Eintraege gefunden.'
}

Add-Line ''
Add-Line '=== DUMP-DATEIEN (.dmp) ==='
$dumps = @()
$dumps += Get-ChildItem -LiteralPath (Join-Path $WER 'Temp') -Filter '*.dmp' -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match $regexPattern }
foreach ($r in ($reports | Select-Object -First 10)) {
    $dumps += Get-ChildItem -LiteralPath $r.FullName -Filter '*.dmp' -File -ErrorAction SilentlyContinue
}
if ($dumps.Count -gt 0) {
    foreach ($d in $dumps) {
        Add-Line "DMP: $($d.FullName) | $($d.Length) Bytes | $($d.LastWriteTime)"
    }
}
else {
    Add-Line 'Keine passenden DMP-Dateien gefunden.'
}

# 3) Windows Application Event Log (IDs 1000, 1001)
Add-Line ''
Add-Line '=== WINDOWS APPLICATION EVENTS (ID 1000, 1001) ==='
$events = @(Get-WinEvent -FilterHashtable @{
    LogName   = 'Application'
    Id        = 1000, 1001
    StartTime = (Get-Date).AddDays(-$LookbackDays)
} -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -match $regexPattern } |
    Select-Object -First 15)

if ($events.Count -gt 0) {
    foreach ($ev in $events) {
        Add-Line ("[{0}] EventID={1} Provider={2}" -f $ev.TimeCreated, $ev.Id, $ev.ProviderName)
        $firstLines = ($ev.Message -split "`r?`n" | Select-Object -First 6) -join ' | '
        Add-Line "  $firstLines"
    }
}
else {
    Add-Line 'Keine passenden Application-Crash-Events im Zeitraum gefunden.'
}

Add-Line ''
Add-Line '=== DIAGNOSE BEENDET ==='
$lines | Set-Content -LiteralPath $ReportFile -Encoding UTF8
Write-Host "Crash-Report gespeichert: $ReportFile" -ForegroundColor Green
