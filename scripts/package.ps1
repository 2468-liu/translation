# PowerShell Distribution Packager for Real-time Subtitle & Translation
[CmdletBinding()]
param(
    [string]$DistDir = "",
    [switch]$CreateZip
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
if (-not $DistDir) {
    $DistDir = Join-Path $RepoRoot "dist"
}
$TargetDir = Join-Path $DistDir "translation-release"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Building standalone distribution package " -ForegroundColor Cyan
Write-Host " Source: $RepoRoot" -ForegroundColor Gray
Write-Host " Target: $TargetDir" -ForegroundColor Gray
Write-Host "==========================================" -ForegroundColor Cyan

if (Test-Path $TargetDir) {
    Remove-Item -Recurse -Force $TargetDir
}
New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

# 1. Copy server module
Write-Host "[1/5] Copying server/ ..." -ForegroundColor Green
Copy-Item -Recurse (Join-Path $RepoRoot "server") (Join-Path $TargetDir "server") -Exclude "__pycache__","*.pyc"

# 2. Copy extension module
Write-Host "[2/5] Copying extension/ ..." -ForegroundColor Green
Copy-Item -Recurse (Join-Path $RepoRoot "extension") (Join-Path $TargetDir "extension")

# 3. Copy runtime scripts
Write-Host "[3/5] Copying production scripts/ ..." -ForegroundColor Green
$ScriptTarget = Join-Path $TargetDir "scripts"
New-Item -ItemType Directory -Path $ScriptTarget -Force | Out-Null
$ProductionScripts = @(
    "start.ps1",
    "resolve_llama.ps1",
    "install_native_host.ps1",
    "build_native_host.ps1",
    "native_host.py",
    "download_models.py",
    "minimal_vad_server.py"
)
foreach ($scriptName in $ProductionScripts) {
    $src = Join-Path $RepoRoot "scripts\$scriptName"
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $ScriptTarget $scriptName)
    }
}

# 4. Copy docs and metadata
Write-Host "[4/5] Copying docs & requirements ..." -ForegroundColor Green
$Docs = @("README.md", "ARCHIVE.md", "STATE.md", "REPORT.md", "requirements.txt")
foreach ($doc in $Docs) {
    $src = Join-Path $RepoRoot $doc
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $TargetDir $doc)
    }
}

# Ensure logs dir exists with .gitkeep
$LogsTarget = Join-Path $TargetDir "logs"
New-Item -ItemType Directory -Path $LogsTarget -Force | Out-Null
Set-Content -Path (Join-Path $LogsTarget ".gitkeep") -Value "" -Encoding UTF8

# Copy run meta files
Get-ChildItem -Path (Join-Path $RepoRoot "logs") -Filter "run.*.meta" -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $LogsTarget $_.Name)
}

# Clean pycache inside target
Get-ChildItem -Path $TargetDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 5. Optional Zip packaging
if ($CreateZip) {
    $ZipPath = Join-Path $DistDir "translation-release.zip"
    Write-Host "[5/5] Compressing to $ZipPath ..." -ForegroundColor Green
    if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
    Compress-Archive -Path "$TargetDir\*" -DestinationPath $ZipPath -Force
}

Write-Host "`n[SUCCESS] Package successfully created at: $TargetDir" -ForegroundColor Cyan
