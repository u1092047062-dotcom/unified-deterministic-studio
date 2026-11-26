#requires -Version 5.1
<#
    collect_crash_report.ps1
    SAFE READ-ONLY CRASH DIAGNOSTIC fuer Unified Deterministic Studio.
#>

[CmdletBinding()]
param(
    [string[]]$ExecutableNames = @('eda-cad-studio.exe', 'rust-pc-remote.exe', 'tpai.exe', 'gigapixel.exe'),
    [string]$CargoTargetDir = 'D:\MEINE_PROJEKTE\.cargo_target_cache',
    [int]$LookbackDays = 7,
    [string]$ReportFile = ''
)

$ErrorActionPreference = 'Continue'
$PlatformRoot = Split-Path -Parent $PSScriptRoot
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'

if ([string]::IsNullOrWhiteSpace($ReportFile)) {
    $verifyDir = Join-Path $PlatformRoot 'verification'
    New-Item -ItemType Directory -Path $verifyDir -Force | Out-Null
    $ReportFile = Join-Path $verifyDir "unified_crash_report_$Stamp.txt"
}

$WER = Join-Path $env:ProgramData 'Microsoft\Windows\WER'
$baseNames = @($ExecutableNames | ForEach-Object { [System.IO.Path]::GetFileNameWithoutExtension($_) })
$pattern = ($baseNames | ForEach-Object { [regex]::Escape($_) }) -join '|'

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('=== UNIFIED CRASH REPORT (READ-ONLY) ===')
$lines.Add("Zeit: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$lines.Add("Executables: $($ExecutableNames -join ', ')")

$releaseDir = Join-Path $CargoTargetDir 'release'
foreach ($exe in $ExecutableNames) {
    $p = Join-Path $releaseDir $exe
    if (Test-Path -LiteralPath $p -PathType Leaf) {
        $hash = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash
        $lines.Add("BINARY: $p | SHA256=$hash")
    }
}

$reports = @(Get-ChildItem -LiteralPath (Join-Path $WER 'ReportArchive') -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match $pattern } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 20)
$lines.Add("WER-Reports: $($reports.Count)")
foreach ($r in $reports) { $lines.Add("  WER: $($r.FullName) | $($r.LastWriteTime)") }

$events = @(Get-WinEvent -FilterHashtable @{ LogName = 'Application'; Id = 1000, 1001; StartTime = (Get-Date).AddDays(-$LookbackDays) } -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -match $pattern } |
    Select-Object -First 15)
$lines.Add("Application-Events: $($events.Count)")
foreach ($ev in $events) { $lines.Add("  [$($ev.TimeCreated)] ID=$($ev.Id) $($ev.ProviderName)") }

$lines | Set-Content -LiteralPath $ReportFile -Encoding UTF8
Write-Host "Crash-Report gespeichert: $ReportFile" -ForegroundColor Green
