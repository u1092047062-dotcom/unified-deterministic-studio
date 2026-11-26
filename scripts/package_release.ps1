#requires -Version 5.1
<#
    package_release.ps1
    Baut und signiert (SHA-256-Manifest) die lokalen Release-Artefakte mit RAM-/Laufwerksschutz:
    - Setzt CARGO_TARGET_DIR und CARGO_BUILD_JOBS=2.
    - Baut services/terra-remote-engine (--release --locked).
    - Erfasst SHA-256 + Bytegroesse fuer gebaute Binaries in dist/release_manifest_<timestamp>.json.
    - Startet keine Dienste und aendert keine Windows-Aufgaben.
#>

[CmdletBinding()]
param(
    [string]$CargoTargetDir = 'D:\MEINE_PROJEKTE\.cargo_target_cache',
    [int]$CargoJobs = 2,
    [switch]$SkipTerraBuild
)

$ErrorActionPreference = 'Stop'
$PlatformRoot = Split-Path -Parent $PSScriptRoot
$TerraRoot = Join-Path $PlatformRoot 'services\terra-remote-engine'
$DistDir = Join-Path $PlatformRoot 'dist'
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

$oldTarget = $env:CARGO_TARGET_DIR
$oldJobs = $env:CARGO_BUILD_JOBS

try {
    $env:CARGO_TARGET_DIR = $CargoTargetDir
    $env:CARGO_BUILD_JOBS = [string]$CargoJobs

    if (-not $SkipTerraBuild) {
        Write-Host '=== Baue terra-remote-engine (Release, --locked, --jobs 2) ===' -ForegroundColor Cyan
        Push-Location -LiteralPath $TerraRoot
        try {
            cargo build --release --locked --jobs $CargoJobs
            if ($LASTEXITCODE -ne 0) { throw "Release-Build fehlgeschlagen (Exitcode $LASTEXITCODE)." }
        }
        finally { Pop-Location }
    }

    $binaries = @(
        (Join-Path $CargoTargetDir 'release\rust-pc-remote.exe'),
        (Join-Path $CargoTargetDir 'release\eda-cad-studio.exe')
    )

    $artifacts = @{}
    foreach ($bin in $binaries) {
        if (Test-Path -LiteralPath $bin -PathType Leaf) {
            $item = Get-Item -LiteralPath $bin
            $hash = (Get-FileHash -LiteralPath $bin -Algorithm SHA256).Hash
            $artifacts[$item.Name] = @{
                path       = $item.FullName
                size_bytes = $item.Length
                sha256     = $hash
                last_write = $item.LastWriteTimeUtc.ToString('o')
            }
        }
    }

    $gitCommit = ''
    try { $gitCommit = (git -C $PlatformRoot rev-parse HEAD 2>$null).Trim() } catch {}

    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $manifestPath = Join-Path $DistDir "release_manifest_$stamp.json"
    $manifest = @{
        platform         = 'unified-deterministic-studio'
        generated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        git_commit       = $gitCommit
        cargo_target_dir = $CargoTargetDir
        artifacts        = $artifacts
    } | ConvertTo-Json -Depth 5

    $manifest | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Host "Release-Manifest erstellt: $manifestPath" -ForegroundColor Green
    Write-Host $manifest
}
finally {
    if ($null -eq $oldTarget) { Remove-Item Env:\CARGO_TARGET_DIR -ErrorAction SilentlyContinue } else { $env:CARGO_TARGET_DIR = $oldTarget }
    if ($null -eq $oldJobs) { Remove-Item Env:\CARGO_BUILD_JOBS -ErrorAction SilentlyContinue } else { $env:CARGO_BUILD_JOBS = $oldJobs }
}
