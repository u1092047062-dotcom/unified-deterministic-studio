#requires -Version 5.1
<#
    verify_unified_platform.ps1
    Fail-Closed End-to-End-Verifikation fuer Unified Deterministic Studio:
    1. packages/platform_core (pytest)
    2. apps/eda-multiagent-core (pytest)
    3. apps/art-restoration-ai (pytest)
    4. apps/eda-multiagent-core/rust_core (cargo fmt --check + cargo test --no-default-features --locked)
    5. apps/eda-multiagent-core/src-tauri (cargo test --locked)
    6. services/terra-remote-engine (cargo fmt --check + cargo test --all-targets --locked + clippy -D warnings)
    7. Plattformweiter Secret-Hygiene-Check (0 offene Tokens, 0 unerlaubte .env*-Dateien)
#>

[CmdletBinding()]
param(
    [string]$CargoTargetDir = 'D:\MEINE_PROJEKTE\.cargo_target_cache',
    [int]$CargoJobs = 2,
    [string]$PythonExe = '',
    [switch]$PauseOnExit
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$PlatformRoot = Split-Path -Parent $PSScriptRoot
$CorePkgRoot = Join-Path $PlatformRoot 'packages\platform_core'
$EdaRoot = Join-Path $PlatformRoot 'apps\eda-multiagent-core'
$ArtRoot = Join-Path $PlatformRoot 'apps\art-restoration-ai'
$RustCoreRoot = Join-Path $EdaRoot 'rust_core'
$TauriRoot = Join-Path $EdaRoot 'src-tauri'
$TerraRoot = Join-Path $PlatformRoot 'services\terra-remote-engine'

$ReportDir = Join-Path $PlatformRoot 'verification'
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$ReportFile = Join-Path $ReportDir "unified_verify_$Stamp.txt"

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

function Resolve-PythonInterpreter {
    if (-not [string]::IsNullOrWhiteSpace($PythonExe) -and (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        return $PythonExe
    }
    $candidates = @(
        (Join-Path $PlatformRoot '.venv\Scripts\python.exe'),
        (Join-Path $EdaRoot 'venv\Scripts\python.exe'),
        'D:\MEINE_PROJEKTE\.PMM_STAGING_20261121_124709\05_PLATINEN_ELEKTRONIK\eda-multiagent-core\venv\Scripts\python.exe'
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c -PathType Leaf) { return $c }
    }
    return 'python'
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
    param([Parameter(Mandatory = $true)][string]$ScanRoot)
    $stepName = '7. Plattformweiter Secret-Hygiene-Check'
    Write-Log -Message $stepName -Level 'SECTION'

    $findings = New-Object System.Collections.Generic.List[string]
    $excludedDirRegex = '(?i)\\(venv|\.venv|target|node_modules|\.pytest_cache|__pycache__|verification|logs|data|work|_backups|\.git)(\\|$)'

    $allFiles = Get-ChildItem -LiteralPath $ScanRoot -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch $excludedDirRegex }

    foreach ($file in $allFiles) {
        $nameLower = $file.Name.ToLowerInvariant()
        if (($nameLower -eq '.env' -or $nameLower -like '.env.*') -and $nameLower -ne '.env.example') {
            $findings.Add("Unerlaubte ENV-Datei: $($file.FullName)")
        }
        if ($nameLower -like '*telegrambottoken*' -or $nameLower -eq 'credentials.json' -or $nameLower -eq 'secrets.json') {
            $findings.Add("Potenzielle Secret-Datei: $($file.FullName)")
        }
    }

    $textExts = @('.py', '.rs', '.toml', '.json', '.md', '.txt', '.ps1', '.cmd', '.yml', '.yaml', '.html', '.js', '.ts', '.css', '.example')
    $patterns = @(
        @{ Name = 'Telegram Bot Token'; Regex = '\b\d{8,12}:[A-Za-z0-9_-]{30,}\b' },
        @{ Name = 'OpenAI/API Secret Key'; Regex = '\bsk-[A-Za-z0-9_-]{20,}\b' },
        @{ Name = 'GitHub Token'; Regex = '\bgh[pousr]_[A-Za-z0-9]{20,}\b' },
        @{ Name = 'AWS Access Key'; Regex = '\bAKIA[0-9A-Z]{16}\b' },
        @{ Name = 'Private Key Block'; Regex = '-----BEGIN [A-Z ]*PRIVATE KEY-----' }
    )

    foreach ($file in $allFiles) {
        if ($textExts -notcontains $file.Extension.ToLowerInvariant() -and $file.Name -ne '.env.example') { continue }
        if ($file.Length -gt 2MB) { continue }
        try {
            $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 -ErrorAction Stop
            if ([string]::IsNullOrEmpty($content)) { continue }
            foreach ($pat in $patterns) {
                if ([regex]::IsMatch($content, $pat.Regex)) {
                    $findings.Add("$($pat.Name) Treffer in: $($file.FullName)")
                }
            }
        }
        catch {}
    }

    if (-not (Test-Path -LiteralPath (Join-Path $ScanRoot '.gitignore') -PathType Leaf)) {
        $findings.Add('Fehlende Root-.gitignore')
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
$oldTarget = $env:CARGO_TARGET_DIR
$oldJobs = $env:CARGO_BUILD_JOBS
$oldPyPath = $env:PYTHONPATH

try {
    $env:CARGO_TARGET_DIR = $CargoTargetDir
    $env:CARGO_BUILD_JOBS = [string]$CargoJobs
    $extraSite = Join-Path $PlatformRoot '.venv\site-packages'
    $pyPaths = @($CorePkgRoot)
    if (Test-Path -LiteralPath $extraSite -PathType Container) { $pyPaths += $extraSite }
    if (-not [string]::IsNullOrWhiteSpace($oldPyPath)) { $pyPaths += $oldPyPath }
    $env:PYTHONPATH = ($pyPaths -join ';')

    $py = Resolve-PythonInterpreter
    Write-Log -Message 'Unified Deterministic Studio – Fail-Closed Verifikation gestartet' -Level 'SECTION'
    Write-Log -Message "PlatformRoot:     $PlatformRoot"
    Write-Log -Message "Python:           $py"
    Write-Log -Message "CARGO_TARGET_DIR: $($env:CARGO_TARGET_DIR)"
    Write-Log -Message "CARGO_BUILD_JOBS: $($env:CARGO_BUILD_JOBS)"

    Invoke-Step -Name '1. platform_core pytest' -WorkingDirectory $CorePkgRoot -FilePath $py -Arguments @('-m', 'pytest', 'tests', '-v') | Out-Null
    Invoke-Step -Name '2. eda-multiagent-core pytest' -WorkingDirectory $EdaRoot -FilePath $py -Arguments @('-m', 'pytest', '-v') | Out-Null
    Invoke-Step -Name '3. art-restoration-ai pytest' -WorkingDirectory $ArtRoot -FilePath $py -Arguments @('-m', 'pytest', '-v') | Out-Null
    Invoke-Step -Name '4a. rust_core cargo fmt --check' -WorkingDirectory $RustCoreRoot -FilePath 'cargo' -Arguments @('fmt', '--all', '--', '--check') | Out-Null
    Invoke-Step -Name '4b. rust_core cargo test' -WorkingDirectory $RustCoreRoot -FilePath 'cargo' -Arguments @('test', '--no-default-features', '--locked', '--jobs', [string]$CargoJobs) | Out-Null
    Invoke-Step -Name '5. src-tauri cargo test' -WorkingDirectory $TauriRoot -FilePath 'cargo' -Arguments @('test', '--locked', '--jobs', [string]$CargoJobs) | Out-Null
    Invoke-Step -Name '6a. terra-remote-engine cargo fmt --check' -WorkingDirectory $TerraRoot -FilePath 'cargo' -Arguments @('fmt', '--all', '--', '--check') | Out-Null
    Invoke-Step -Name '6b. terra-remote-engine cargo test' -WorkingDirectory $TerraRoot -FilePath 'cargo' -Arguments @('test', '--all-targets', '--locked', '--jobs', [string]$CargoJobs) | Out-Null
    Invoke-Step -Name '6c. terra-remote-engine cargo clippy' -WorkingDirectory $TerraRoot -FilePath 'cargo' -Arguments @('clippy', '--all-targets', '--locked', '--jobs', [string]$CargoJobs, '--', '-D', 'warnings') | Out-Null
    Invoke-SecretHygieneCheck -ScanRoot $PlatformRoot | Out-Null
}
finally {
    if ($null -eq $oldTarget) { Remove-Item Env:\CARGO_TARGET_DIR -ErrorAction SilentlyContinue } else { $env:CARGO_TARGET_DIR = $oldTarget }
    if ($null -eq $oldJobs) { Remove-Item Env:\CARGO_BUILD_JOBS -ErrorAction SilentlyContinue } else { $env:CARGO_BUILD_JOBS = $oldJobs }
    if ($null -eq $oldPyPath) { Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue } else { $env:PYTHONPATH = $oldPyPath }
}

$duration = (Get-Date) - $startedAt
Write-Log -Message 'Zusammenfassung' -Level 'SECTION'
foreach ($r in $script:StepResults) {
    $lvl = if ($r.Status -eq 'PASS') { 'OK' } else { 'FAIL' }
    Write-Log -Message ("{0,-44} => {1} (Exit {2})" -f $r.Step, $r.Status, $r.ExitCode) -Level $lvl
}
Write-Log -Message ("Gesamtdauer:  " + $duration.ToString())
Write-Log -Message ("Fehleranzahl: " + $script:Failures.Count)

if ($script:Failures.Count -eq 0) {
    Write-Log -Message 'GESAMTERGEBNIS: PASS (Alle Pflichtpruefungen bestanden)' -Level 'OK'
    $exitCode = 0
}
else {
    Write-Log -Message 'GESAMTERGEBNIS: FAIL' -Level 'FAIL'
    foreach ($fail in $script:Failures) { Write-Log -Message ("FEHLER: " + $fail) -Level 'FAIL' }
    $exitCode = 1
}

$script:ReportLines | Set-Content -LiteralPath $ReportFile -Encoding UTF8
Write-Host "`nPruefbericht gespeichert: $ReportFile" -ForegroundColor Cyan

if ($PauseOnExit) { Read-Host 'Enter zum Schliessen' }
exit $exitCode
