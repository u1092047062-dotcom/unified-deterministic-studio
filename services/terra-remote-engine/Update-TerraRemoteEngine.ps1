#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$ApplyScheduledTask,
    [string]$TaskName = 'TerraRemoteEngine'
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$NewExe = Join-Path $ProjectRoot 'target\release\rust-pc-remote.exe'
if (-not (Test-Path -LiteralPath $NewExe -PathType Leaf)) { throw "Release-EXE nicht gefunden. Zuerst .\VERIFY_AND_BUILD.ps1 erfolgreich ausführen: $NewExe" }
$NewExe = [IO.Path]::GetFullPath($NewExe)
$ExeHash = (Get-FileHash -LiteralPath $NewExe -Algorithm SHA256).Hash
Write-Host "TerraRemoteEngine 1.2 – Deployment-Prüfung" -ForegroundColor Cyan
Write-Host "Projekt: $ProjectRoot"
Write-Host "EXE: $NewExe"
Write-Host "SHA256: $ExeHash"

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$Current = $Task.Actions | Select-Object -First 1
Write-Host "Aktuelle Aktion:" -ForegroundColor Cyan
$Current | Format-List Execute,Arguments,WorkingDirectory
if (-not $ApplyScheduledTask) {
    Write-Warning 'Nur Prüfung: Die Windows-Aufgabe wurde NICHT verändert. Zum bewussten Anwenden erneut mit -ApplyScheduledTask starten.'
    return
}

$BackupDir = Join-Path $ProjectRoot 'deployment_backups'
New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
$BackupFile = Join-Path $BackupDir ("{0}_{1}.xml" -f $TaskName,(Get-Date -Format 'yyyyMMdd_HHmmss'))
Export-ScheduledTask -TaskName $TaskName | Set-Content -LiteralPath $BackupFile -Encoding Unicode
if (-not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) { throw 'Aufgaben-Backup konnte nicht erstellt werden; Deployment abgebrochen.' }

$Action = New-ScheduledTaskAction -Execute $NewExe -WorkingDirectory $ProjectRoot
Set-ScheduledTask -TaskName $TaskName -Action $Action -ErrorAction Stop | Out-Null
$Updated = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$Actual = $Updated.Actions | Select-Object -First 1
if ([IO.Path]::GetFullPath($Actual.Execute) -ine $NewExe -or [IO.Path]::GetFullPath($Actual.WorkingDirectory) -ine [IO.Path]::GetFullPath($ProjectRoot)) {
    throw "Gespeicherte Aufgabenaktion weicht ab. Vorheriges XML-Backup: $BackupFile"
}
Write-Host "Aufgabenaktion aktualisiert und geprüft. Backup: $BackupFile" -ForegroundColor Green
Write-Host 'Hinweis: Die Aufgabe wurde nicht gestartet. Der Dienst-/Bot-Betrieb muss separat kontrolliert werden.'
