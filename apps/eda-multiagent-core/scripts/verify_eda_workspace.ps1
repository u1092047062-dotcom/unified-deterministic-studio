#requires -Version 5.1
<#
    verify_eda_workspace.ps1
    Fail-Closed End-to-End-Verifikation fuer 05_PLATINEN_ELEKTRONIK / eda-multiagent-core.
    - Setzt CARGO_TARGET_DIR und CARGO_BUILD_JOBS zum RAM-/Laufwerksschutz.
    - Fuehrt Python pytest, rust_core, src-tauri, terra-remote-engine und Secret-Hygiene-Check aus.
    - Protokolliert vollstaendig nach verification/eda_verify_<timestamp>.txt.
    - Veraendert keine Quelldateien, startet keine Hintergrunddienste oder Windows-Aufgaben.
#>

[CmdletBinding()]
param(
    [string]$CargoTargetDir = 'D:\MEINE_PROJEKTE\.cargo_target_cache',
    [int]$CargoJobs = 2,
    [switch]$PauseOnExit
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$EdaRoot = Split-Path -Parent $PSScriptRoot
$ProjectRoot = Split-Path -Parent $EdaRoot
$TerraRoot = Join-Path $ProjectRoot 'tools\monitoring\terra-remote-engine'
$RustCoreRoot = Join-Path $EdaRoot 'rust_core'
$TauriRoot = Join-Path $EdaRoot 'src-tauri'

$ReportDir = Join-Path $EdaRoot 'verification'
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$ReportFile = Join-Path $ReportDir "eda_verify_$Stamp.txt"

$script:Failures = New-Object System.Collections.Generic.List[string]
$script:ReportLines = New-Object System.Collections.Generic.List[string]
$script:StepResults = New-Object System.Collections.Generic.List[psobject]

function Write-Log {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet('INFO', 'OK', 'WARN', 'FAIL', 'SECTION')][string]$Level = 'INFO'
    )

    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] [$Level] $Message"
    $script:ReportLines.Add($line)

    switch ($Level) {
        'OK'      { Write-Host $line -ForegroundColor Green }
        'WARN'    { Write-Host $line -ForegroundColor Yellow }
        'FAIL'    { Write-Host $line -ForegroundColor Red }
        'SECTION' { Write-Host "`n$line" -ForegroundColor Cyan }
        default   { Write-Host $line -ForegroundColor Gray }
    }
}

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Write-Log -Message $Name -Level 'SECTION'
    Write-Log -Message ("Dir: " + $WorkingDirectory)
    Write-Log -Message ("Cmd: " + $FilePath + ' ' + ($Arguments -join ' '))

    if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
        $msg = "Verzeichnis fehlt: $WorkingDirectory"
        Write-Log -Message $msg -Level 'FAIL'
        $script:Failures.Add("$Name -> $msg")
        $script:StepResults.Add([pscustomobject]@{ Step = $Name; Status = 'FAIL'; ExitCode = -1 })
        return $false
    }

    Push-Location -LiteralPath $WorkingDirectory
    try {
        $output = @(& $FilePath @Arguments 2>&1)
        $code = $LASTEXITCODE
        foreach ($entry in $output) {
            $text = [string]$entry
            if (-not [string]::IsNullOrWhiteSpace($text)) {
                Write-Log -Message $text
            }
        }

        if ($code -eq 0) {
            Write-Log -Message "$Name`: PASS (Exitcode 0)" -Level 'OK'
            $script:StepResults.Add([pscustomobject]@{ Step = $Name; Status = 'PASS'; ExitCode = 0 })
            return $true
        }

        Write-Log -Message "$Name`: FAIL (Exitcode $code)" -Level 'FAIL'
        $script:Failures.Add("$Name (Exitcode $code)")
        $script:StepResults.Add([pscustomobject]@{ Step = $Name; Status = 'FAIL'; ExitCode = $code })
        return $false
    }
    catch {
        Write-Log -Message ("$Name`: Ausnahme - " + $_.Exception.Message) -Level 'FAIL'
        $script:Failures.Add("$Name (" + $_.Exception.Message + ")")
        $script:StepResults.Add([pscustomobject]@{ Step = $Name; Status = 'FAIL'; ExitCode = -1 })
        return $false
    }
    finally {
        Pop-Location
    }
}

function Invoke-SecretHygieneCheck {
    param(
        [Parameter(Mandatory = $true)][string]$ScanRoot
    )

    $stepName = '5. Secret-Hygiene-Check'
    Write-Log -Message $stepName -Level 'SECTION'
    Write-Log -Message "ScanRoot: $ScanRoot"

    $findings = New-Object System.Collections.Generic.List[string]
    $excludedDirRegex = '(?i)\\(venv|\.venv|target|node_modules|\.pytest_cache|__pycache__|verification|_backups|deployment_backups|\.git)(\\|$)'

    # 1) Pruefe auf unerlaubte Secret-Dateinamen (.env*, *telegrambottoken*, credentials.json, secrets.json)
    $allFiles = Get-ChildItem -LiteralPath $ScanRoot -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch $excludedDirRegex }

    foreach ($file in $allFiles) {
        $nameLower = $file.Name.ToLowerInvariant()
        if (($nameLower -eq '.env' -or $nameLower -like '.env.*') -and $nameLower -ne '.env.example') {
            $findings.Add("Unerlaubte ENV-Datei gefunden: $($file.FullName)")
            continue
        }
        if ($nameLower -like '*telegrambottoken*' -or $nameLower -eq 'credentials.json' -or $nameLower -eq 'secrets.json') {
            $findings.Add("Potenzielle Secret-Datei gefunden: $($file.FullName)")
            continue
        }
    }

    # 2) Pruefe Text-/Code-Dateien auf offene Tokens / Private Keys
    $textExtensions = @('.py', '.rs', '.toml', '.json', '.md', '.txt', '.ps1', '.cmd', '.yml', '.yaml', '.html', '.js', '.ts', '.css', '.example')
    $patterns = @(
        @{ Name = 'Telegram Bot Token'; Regex = '\b\d{8,12}:[A-Za-z0-9_-]{30,}\b' },
        @{ Name = 'OpenAI/API Secret Key'; Regex = '\bsk-[A-Za-z0-9_-]{20,}\b' },
        @{ Name = 'GitHub Token'; Regex = '\bgh[pousr]_[A-Za-z0-9]{20,}\b' },
        @{ Name = 'AWS Access Key'; Regex = '\bAKIA[0-9A-Z]{16}\b' },
        @{ Name = 'Private Key Block'; Regex = '-----BEGIN [A-Z ]*PRIVATE KEY-----' }
    )

    foreach ($file in $allFiles) {
        if ($textExtensions -notcontains $file.Extension.ToLowerInvariant() -and $file.Name -ne '.env.example') {
            continue
        }
        if ($file.Length -gt 2MB) {
            continue
        }
        try {
            $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 -ErrorAction Stop
            if ([string]::IsNullOrEmpty($content)) { continue }
            foreach ($pat in $patterns) {
                if ([regex]::IsMatch($content, $pat.Regex)) {
                    $findings.Add("$($pat.Name) Treffer in: $($file.FullName)")
                }
            }
        }
        catch {
            Write-Log -Message ("Uebersprungen (nicht als UTF-8 lesbar): " + $file.FullName) -Level 'WARN'
        }
    }

    # 3) Falls Git-Repo vorhanden: pruefe getrackte/unignorierte .env- und Archivdateien
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd -and (Test-Path -LiteralPath (Join-Path $ScanRoot '.git'))) {
        Push-Location -LiteralPath $ScanRoot
        try {
            $tracked = @(& $gitCmd.Source ls-files 2>&1)
            foreach ($rel in $tracked) {
                if ($rel -match '(?i)(^|/)\.env($|\.(?!example$))|telegrambottoken|\.(zip|rar|dmp|exe|dll|pdb)$') {
                    $findings.Add("Git-getrackte Sperrdatei: $rel")
                }
            }
        }
        finally {
            Pop-Location
        }
    }

    # 4) Pruefe Existenz beider .gitignore-Dateien
    foreach ($gi in @((Join-Path $ScanRoot '.gitignore'), (Join-Path $EdaRoot '.gitignore'))) {
        if (-not (Test-Path -LiteralPath $gi -PathType Leaf)) {
            $findings.Add("Fehlende .gitignore: $gi")
        }
    }

    if ($findings.Count -eq 0) {
        Write-Log -Message "$stepName`: PASS (0 offene Secrets/Tokens, 0 unerlaubte .env-Dateien)" -Level 'OK'
        $script:StepResults.Add([pscustomobject]@{ Step = $stepName; Status = 'PASS'; ExitCode = 0 })
        return $true
    }

    foreach ($f in $findings) {
        Write-Log -Message "FINDING: $f" -Level 'FAIL'
        $script:Failures.Add("$stepName -> $f")
    }
    $script:StepResults.Add([pscustomobject]@{ Step = $stepName; Status = 'FAIL'; ExitCode = 1 })
    return $false
}

$startedAt = Get-Date
$oldCargoTargetDir = $env:CARGO_TARGET_DIR
$oldCargoBuildJobs = $env:CARGO_BUILD_JOBS

try {
    $env:CARGO_TARGET_DIR = $CargoTargetDir
    $env:CARGO_BUILD_JOBS = [string]$CargoJobs

    Write-Log -Message 'EDA Workspace Fail-Closed Verifikation gestartet' -Level 'SECTION'
    Write-Log -Message "ProjectRoot:      $ProjectRoot"
    Write-Log -Message "EdaRoot:          $EdaRoot"
    Write-Log -Message "CARGO_TARGET_DIR: $($env:CARGO_TARGET_DIR)"
    Write-Log -Message "CARGO_BUILD_JOBS: $($env:CARGO_BUILD_JOBS)"

    # 1) Python pytest
    $venvPython = Join-Path $EdaRoot 'venv\Scripts\python.exe'
    $pythonExe = if (Test-Path -LiteralPath $venvPython -PathType Leaf) { $venvPython } else { 'python' }
    Write-Log -Message "Python-Interpreter: $pythonExe"
    Invoke-Step -Name '1. Python pytest Suite' -WorkingDirectory $EdaRoot -FilePath $pythonExe -Arguments @('-m', 'pytest', '-v') | Out-Null

    # 2) rust_core
    Invoke-Step -Name '2a. rust_core cargo fmt --check' -WorkingDirectory $RustCoreRoot -FilePath 'cargo' -Arguments @('fmt', '--all', '--', '--check') | Out-Null
    Invoke-Step -Name '2b. rust_core cargo test' -WorkingDirectory $RustCoreRoot -FilePath 'cargo' -Arguments @('test', '--no-default-features', '--locked', '--jobs', [string]$CargoJobs) | Out-Null

    # 3) src-tauri
    Invoke-Step -Name '3. src-tauri cargo test' -WorkingDirectory $TauriRoot -FilePath 'cargo' -Arguments @('test', '--locked', '--jobs', [string]$CargoJobs) | Out-Null

    # 4) tools/monitoring/terra-remote-engine
    Invoke-Step -Name '4a. terra-remote-engine cargo fmt --check' -WorkingDirectory $TerraRoot -FilePath 'cargo' -Arguments @('fmt', '--all', '--', '--check') | Out-Null
    Invoke-Step -Name '4b. terra-remote-engine cargo test' -WorkingDirectory $TerraRoot -FilePath 'cargo' -Arguments @('test', '--all-targets', '--locked', '--jobs', [string]$CargoJobs) | Out-Null
    Invoke-Step -Name '4c. terra-remote-engine cargo clippy' -WorkingDirectory $TerraRoot -FilePath 'cargo' -Arguments @('clippy', '--all-targets', '--locked', '--jobs', [string]$CargoJobs, '--', '-D', 'warnings') | Out-Null

    # 5) Secret-Hygiene-Check
    Invoke-SecretHygieneCheck -ScanRoot $ProjectRoot | Out-Null
}
finally {
    if ($null -eq $oldCargoTargetDir) { Remove-Item Env:\CARGO_TARGET_DIR -ErrorAction SilentlyContinue } else { $env:CARGO_TARGET_DIR = $oldCargoTargetDir }
    if ($null -eq $oldCargoBuildJobs) { Remove-Item Env:\CARGO_BUILD_JOBS -ErrorAction SilentlyContinue } else { $env:CARGO_BUILD_JOBS = $oldCargoBuildJobs }
}

$duration = (Get-Date) - $startedAt
Write-Log -Message 'Zusammenfassung' -Level 'SECTION'
foreach ($r in $script:StepResults) {
    $lvl = if ($r.Status -eq 'PASS') { 'OK' } else { 'FAIL' }
    Write-Log -Message ("{0,-42} => {1} (Exit {2})" -f $r.Step, $r.Status, $r.ExitCode) -Level $lvl
}
Write-Log -Message ("Gesamtdauer: " + $duration.ToString())
Write-Log -Message ("Fehleranzahl: " + $script:Failures.Count)

if ($script:Failures.Count -eq 0) {
    Write-Log -Message 'GESAMTERGEBNIS: PASS (Fail-Closed Verifikation bestanden)' -Level 'OK'
    $exitCode = 0
}
else {
    Write-Log -Message 'GESAMTERGEBNIS: FAIL (Mindestens ein Pflichtcheck ist fehlgeschlagen)' -Level 'FAIL'
    foreach ($fail in $script:Failures) {
        Write-Log -Message ("FEHLER: " + $fail) -Level 'FAIL'
    }
    $exitCode = 1
}

$script:ReportLines | Set-Content -LiteralPath $ReportFile -Encoding UTF8
Write-Host "`nPruefbericht gespeichert: $ReportFile" -ForegroundColor Cyan

if ($PauseOnExit) {
    Read-Host 'Enter zum Schliessen'
}

exit $exitCode
