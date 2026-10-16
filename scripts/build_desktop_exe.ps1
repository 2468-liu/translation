# PowerShell Build Script for TranslationDesktop.exe
# Implements strict 180s checkpoint log monitoring and fail-fast safety kill.

[CmdletBinding()]
param(
    [string]$PythonExe = "D:\minanconda\envs\pytorch\python.exe",
    [string]$DeployDir = "D:\TranslationDesktop",
    [int]$CheckIntervalSeconds = 180,
    [int]$MaxMemoryMb = 8192
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $RepoRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Building Standalone TranslationDesktop.exe               " -ForegroundColor Cyan
Write-Host " Interval: ${CheckIntervalSeconds}s log checkpoint        " -ForegroundColor Gray
Write-Host " Max RAM:  ${MaxMemoryMb}MB safety threshold              " -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan

$BuildDir = Join-Path $RepoRoot "build"
if (-not (Test-Path $BuildDir)) {
    New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
}
$LogFile = Join-Path $BuildDir "pyinstaller_build.log"
if (Test-Path $LogFile) {
    Remove-Item -Force $LogFile -ErrorAction SilentlyContinue
}

$SpecFile = Join-Path $RepoRoot "TranslationDesktop.spec"
if (-not (Test-Path $SpecFile)) {
    Write-Error "Spec file not found: $SpecFile"
}

$ErrLog = Join-Path $BuildDir "pyinstaller_err.log"
if (Test-Path $ErrLog) {
    Remove-Item -Force $ErrLog -ErrorAction SilentlyContinue
}

Write-Host "[1/3] Launching PyInstaller..." -ForegroundColor Green
$StartTime = [System.Diagnostics.Stopwatch]::StartNew()

$Process = Start-Process -FilePath $PythonExe `
    -ArgumentList @("-m", "PyInstaller", $SpecFile, "--noconfirm") `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrLog `
    -PassThru `
    -NoNewWindow

$ActiveLogFile = $ErrLog
$LastLogLineCount = 0
$IsStuck = $false

try {
    while (-not $Process.WaitForExit($CheckIntervalSeconds * 1000)) {
        $Elapsed = [int]$StartTime.Elapsed.TotalSeconds
        $Proc = Get-Process -Id $Process.Id -ErrorAction SilentlyContinue
        if (-not $Proc) { break }

        $RamMb = [int]($Proc.WorkingSet64 / 1MB)
        Write-Host "`n[Checkpoint ${Elapsed}s] PyInstaller PID=$($Proc.Id), RAM=${RamMb}MB, CPU=$([math]::Round($Proc.TotalProcessorTime.TotalSeconds, 1))s" -ForegroundColor Yellow

        # 1. Check RAM leak
        if ($RamMb -gt $MaxMemoryMb) {
            Write-Host "[FATAL] Memory usage (${RamMb}MB) exceeded limit (${MaxMemoryMb}MB)! Terminating..." -ForegroundColor Red
            $IsStuck = $true
            break
        }

        # 2. Check Log Activity
        if (Test-Path $ActiveLogFile) {
            $Lines = Get-Content -Path $ActiveLogFile -ErrorAction SilentlyContinue
            $CurrentCount = if ($Lines) { $Lines.Count } else { 0 }
            Write-Host "  Log lines: $CurrentCount (+$( $CurrentCount - $LastLogLineCount ))" -ForegroundColor Gray
            if ($CurrentCount -gt 0) {
                $Tail = $Lines | Select-Object -Last 5
                Write-Host "  Last 5 log lines:" -ForegroundColor DarkGray
                $Tail | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }

                # Check for critical errors
                $Errors = $Lines | Select-String -Pattern "Fatal error", "Traceback", "ModuleNotFoundError"
                if ($Errors) {
                    Write-Host "[FATAL] Critical error detected in build log! Terminating..." -ForegroundColor Red
                    $IsStuck = $true
                    break
                }
            }

            if ($CurrentCount -eq $LastLogLineCount) {
                Write-Host "[FATAL] Build stalled: 0 new log lines in the past ${CheckIntervalSeconds}s! Terminating..." -ForegroundColor Red
                $IsStuck = $true
                break
            }
            $LastLogLineCount = $CurrentCount
        }
    }
}
finally {
    if ($IsStuck -or (-not $Process.HasExited -and $IsStuck)) {
        try {
            $Process.Kill()
            Write-Host "[STOP] Killed hung PyInstaller process." -ForegroundColor Red
        } catch {}
    }
}

if ($IsStuck) {
    Write-Error "Build aborted due to safety check violation."
}

$DistExe = Join-Path $RepoRoot "dist\TranslationDesktop\TranslationDesktop.exe"
$Process.WaitForExit()
$ExitCode = $Process.ExitCode
if ($null -eq $ExitCode) {
    $ExitCode = if (Test-Path $DistExe) { 0 } else { 1 }
}
$ElapsedTotal = [math]::Round($StartTime.Elapsed.TotalSeconds, 1)

if ($ExitCode -ne 0) {
    Write-Host "`n[Build FAILED in ${ElapsedTotal}s with ExitCode $ExitCode]" -ForegroundColor Red
    if (Test-Path $ActiveLogFile) {
        Write-Host "Last 20 log lines:" -ForegroundColor DarkRed
        Get-Content $ActiveLogFile -Tail 20
    }
    exit $ExitCode
}

Write-Host "`n[2/3] PyInstaller build succeeded in ${ElapsedTotal}s!" -ForegroundColor Green

# 3. Optional deployment sync
if (Test-Path $DistExe) {
    Write-Host "[3/3] Built executable verified: $DistExe" -ForegroundColor Cyan
    if ($DeployDir -and (Test-Path $DeployDir)) {
        Write-Host "  Checking and stopping running TranslationDesktop instances before sync..." -ForegroundColor Yellow
        Get-Process -Name "TranslationDesktop" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 600

        Write-Host "  Syncing to deploy directory: $DeployDir ..." -ForegroundColor Green
        Copy-Item -Path (Join-Path $RepoRoot "dist\TranslationDesktop\*") -Destination $DeployDir -Recurse -Force
        Copy-Item -Path (Join-Path $RepoRoot "desktop_config.json") -Destination $DeployDir -Force
        Write-Host "  [DEPLOYED] TranslationDesktop and desktop_config.json successfully updated in: $DeployDir" -ForegroundColor Cyan

        # Create and sync Desktop Release ZIP
        $DistFolder = Join-Path $RepoRoot "dist\TranslationDesktop"
        $ZipPath = Join-Path $RepoRoot "dist\TranslationDesktop-Release.zip"
        if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue }
        Write-Host "  Compressing distribution to ZIP archive: $ZipPath ..." -ForegroundColor Yellow
        Compress-Archive -Path "$DistFolder\*" -DestinationPath $ZipPath -Force
        $ZipSizeMb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
        Write-Host "  [ZIP CREATED] Successfully created $ZipPath ($ZipSizeMb MB)" -ForegroundColor Green
        Copy-Item -Path $ZipPath -Destination (Join-Path $DeployDir "TranslationDesktop-Release.zip") -Force
        Write-Host "  [ZIP SYNCED] Synced release ZIP to: $DeployDir" -ForegroundColor Cyan
    }
}
