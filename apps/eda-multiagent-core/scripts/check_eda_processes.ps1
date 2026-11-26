#requires -Version 5.1
<#
    check_eda_processes.ps1
    SAFE READ-ONLY DIAGNOSTIC (parametrisierte Fassung von Telegram_Project_Process_Check.ps1):
    - Listet Prozesse mit Bezug zu 05_PLATINEN_ELEKTRONIK / EDA-Stack auf
      (eda-cad-studio.exe, rust-pc-remote.exe, python.exe/uvicorn, cargo.exe).
    - Beendet, pausiert oder veraendert KEINE Prozesse.
    - Liest KEINE .env- oder Logdatei-Inhalte.
#>

[CmdletBinding()]
param(
    [string]$ProjectRoot = 'D:\MEINE_PROJEKTE\.PMM_STAGING_20261121_124709\05_PLATINEN_ELEKTRONIK',
    [string[]]$ProcessNames = @('eda-cad-studio.exe', 'rust-pc-remote.exe', 'python.exe', 'cargo.exe'),
    [string]$ReportDir = '',
    [switch]$PauseOnExit
)

$ErrorActionPreference = 'Continue'
$EdaRoot = Split-Path -Parent $PSScriptRoot
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'

if ([string]::IsNullOrWhiteSpace($ReportDir)) {
    $ReportDir = Join-Path (Join-Path $EdaRoot 'verification') "eda_process_check_$Stamp"
}
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null

$CsvPath = Join-Path $ReportDir 'process_candidates.csv'
$TxtPath = Join-Path $ReportDir 'process_check.txt'

function Redact-CommandLine {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return '' }
    $redacted = [regex]::Replace($Text, '\b\d{8,12}:[A-Za-z0-9_-]{20,}\b', '[REDACTED_TOKEN]')
    $redacted = [regex]::Replace($redacted, '(?i)(--?(?:token|secret|api[-_]?key|password)\s*[= ]\s*)(\S+)', '$1[REDACTED]')
    return $redacted
}

Write-Host ''
Write-Host 'EDA WORKSPACE - SICHERE PROZESSDIAGNOSE (READ-ONLY)' -ForegroundColor Cyan
Write-Host 'Es werden keine Prozesse beendet oder veraendert.' -ForegroundColor Green
Write-Host "Projekt: $ProjectRoot"
Write-Host ''

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    Write-Warning "Projektordner nicht gefunden: $ProjectRoot"
}

$targetNameSet = @{}
foreach ($n in $ProcessNames) {
    $targetNameSet[$n.ToLowerInvariant()] = $true
}

$processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
$matches = foreach ($p in $processes) {
    $exe = [string]$p.ExecutablePath
    $cmd = [string]$p.CommandLine
    $name = [string]$p.Name
    $nameLower = $name.ToLowerInvariant()
    $combined = "$exe`n$cmd"

    $reason = $null
    if (-not [string]::IsNullOrWhiteSpace($ProjectRoot) -and
        $combined.IndexOf($ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        $reason = 'Projektpfad in EXE-Pfad oder Kommandozeile'
    }
    elseif ($nameLower -eq 'eda-cad-studio.exe' -or $nameLower -eq 'rust-pc-remote.exe') {
        $reason = "EDA-/Monitoring-Binary ($name)"
    }
    elseif ($nameLower -eq 'python.exe' -and ($cmd -match '(?i)uvicorn|server\.server|eda-multiagent-core')) {
        $reason = 'Python/Uvicorn EDA-Server-Prozess'
    }
    elseif ($nameLower -eq 'cargo.exe' -and ($combined -match '(?i)eda-multiagent-core|terra-remote-engine|rust_core|src-tauri')) {
        $reason = 'Cargo-Build-Prozess im EDA-Kontext'
    }
    elseif ($targetNameSet.ContainsKey($nameLower) -and ($combined -match '(?i)05_PLATINEN_ELEKTRONIK')) {
        $reason = 'Zielprozess mit Projektbezug'
    }

    if ($reason) {
        [pscustomobject]@{
            ProcessId      = $p.ProcessId
            Name           = $name
            Reason         = $reason
            ExecutablePath = $exe
            CommandLine    = (Redact-CommandLine -Text $cmd)
        }
    }
}

$sorted = @($matches | Sort-Object Name, ProcessId)
if ($sorted.Count -gt 0) {
    $sorted | Export-Csv -LiteralPath $CsvPath -NoTypeInformation -Encoding UTF8
    Write-Host 'GEFUNDENE ZUGEHOERIGE PROZESSE:' -ForegroundColor Yellow
    $sorted | Format-Table ProcessId, Name, Reason -AutoSize
}
else {
    Write-Host 'Keine laufenden EDA-/Monitoring-Prozesse mit Projektbezug gefunden.' -ForegroundColor Green
}

$report = New-Object System.Collections.Generic.List[string]
$report.Add('EDA WORKSPACE - PROCESS CHECK (READ-ONLY)')
$report.Add("Zeit: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$report.Add("Projekt: $ProjectRoot")
$report.Add("Ueberwachte Executables: $($ProcessNames -join ', ')")
$report.Add("Treffer: $($sorted.Count)")
$report.Add('')

if ($sorted.Count -gt 0) {
    foreach ($m in $sorted) {
        $report.Add("PID: $($m.ProcessId)")
        $report.Add("Name: $($m.Name)")
        $report.Add("Grund: $($m.Reason)")
        $report.Add("EXE: $($m.ExecutablePath)")
        $report.Add("Kommandozeile: $($m.CommandLine)")
        $report.Add('')
    }
}
else {
    $report.Add('Keine Prozesse mit eindeutigem Projektpfad-/EDA-Bezug aktiv.')
}

$report | Set-Content -LiteralPath $TxtPath -Encoding UTF8
Write-Host "Bericht gespeichert: $TxtPath" -ForegroundColor Cyan

if ($PauseOnExit) {
    Read-Host 'Enter zum Schliessen'
}
