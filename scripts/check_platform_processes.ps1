#requires -Version 5.1
<#
    check_platform_processes.ps1
    SAFE READ-ONLY DIAGNOSTIC fuer Unified Deterministic Studio (EDA + ART + Topaz + TerraRemoteEngine).
    Beendet oder veraendert KEINE Prozesse und liest KEINE .env- oder Log-Inhalte.
#>

[CmdletBinding()]
param(
    [string]$ProjectRoot = 'D:\MEINE_PROJEKTE\unified-deterministic-studio',
    [string[]]$ProcessNames = @('eda-cad-studio.exe', 'rust-pc-remote.exe', 'python.exe', 'cargo.exe', 'tpai.exe', 'gigapixel.exe'),
    [string]$ReportDir = '',
    [switch]$PauseOnExit
)

$ErrorActionPreference = 'Continue'
$PlatformRoot = Split-Path -Parent $PSScriptRoot
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
if ([string]::IsNullOrWhiteSpace($ReportDir)) {
    $ReportDir = Join-Path (Join-Path $PlatformRoot 'verification') "platform_process_check_$Stamp"
}
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null

$CsvPath = Join-Path $ReportDir 'process_candidates.csv'
$TxtPath = Join-Path $ReportDir 'process_check.txt'

function Redact-CommandLine {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return '' }
    $r = [regex]::Replace($Text, '\b\d{8,12}:[A-Za-z0-9_-]{20,}\b', '[REDACTED_TOKEN]')
    $r = [regex]::Replace($r, '(?i)(--?(?:token|secret|api[-_]?key|password)\s*[= ]\s*)(\S+)', '$1[REDACTED]')
    return $r
}

$nameSet = @{}
foreach ($n in $ProcessNames) { $nameSet[$n.ToLowerInvariant()] = $true }

$processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
$matches = foreach ($p in $processes) {
    $exe = [string]$p.ExecutablePath
    $cmd = [string]$p.CommandLine
    $name = [string]$p.Name
    $low = $name.ToLowerInvariant()
    $combined = "$exe`n$cmd"

    $reason = $null
    if (-not [string]::IsNullOrWhiteSpace($ProjectRoot) -and $combined.IndexOf($ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        $reason = 'Plattformpfad in EXE oder Kommandozeile'
    }
    elseif ($low -in @('eda-cad-studio.exe', 'rust-pc-remote.exe', 'tpai.exe', 'gigapixel.exe')) {
        $reason = "Plattform-/Monitoring-/Topaz-Binary ($name)"
    }
    elseif ($low -eq 'python.exe' -and ($cmd -match '(?i)uvicorn|server\.server|app\.main|unified-deterministic-studio')) {
        $reason = 'Python/Uvicorn Plattform-Server'
    }
    elseif ($nameSet.ContainsKey($low) -and ($combined -match '(?i)unified-deterministic-studio|eda-multiagent-core|art-restoration-ai|terra-remote-engine')) {
        $reason = 'Zielprozess mit Plattformbezug'
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
    $sorted | Format-Table ProcessId, Name, Reason -AutoSize
}
else {
    Write-Host 'Keine aktiven Plattformprozesse gefunden.' -ForegroundColor Green
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('UNIFIED DETERMINISTIC STUDIO - PROCESS CHECK (READ-ONLY)')
$lines.Add("Zeit: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$lines.Add("Projekt: $ProjectRoot")
$lines.Add("Treffer: $($sorted.Count)")
foreach ($m in $sorted) {
    $lines.Add("PID=$($m.ProcessId) | Name=$($m.Name) | Grund=$($m.Reason) | EXE=$($m.ExecutablePath)")
}
$lines | Set-Content -LiteralPath $TxtPath -Encoding UTF8
Write-Host "Bericht: $TxtPath" -ForegroundColor Cyan
if ($PauseOnExit) { Read-Host 'Enter zum Schliessen' }
