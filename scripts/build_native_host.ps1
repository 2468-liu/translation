$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = if ($env:TRANSLATION_PYTHON) { $env:TRANSLATION_PYTHON } else { "D:\minanconda\envs\pytorch\python.exe" }
$DistDir = "$Root\scripts\native-host"
$OutDir = Join-Path $DistDir "translation-native-host"
$Exe = Join-Path $OutDir "translation-native-host.exe"

# --onedir, not --onefile.
#
# A onefile build is a single exe whose bootloader unpacks itself into
# %TEMP%\_MEI<pid> before running any Python. That unpack is a silent dependency:
# when the bootloader cannot create the directory it prints
# "PYI-<pid>:ERROR Could not create temporary directory!", exits 1, and Chrome sees
# the native messaging port die with nothing to connect to. Observed here with the
# user TEMP set and with TEMP unset -- it only worked when TEMP pointed inside the
# repository. There is no way to see that failure from the popup; the button just
# does nothing.
#
# A onedir build has no unpack step. The bootloader loads the interpreter from
# beside the exe, so there is no temporary directory to fail on and no per-launch
# extraction cost either.
& $Python -m PyInstaller `
    --clean `
    --onedir `
    --console `
    --name translation-native-host `
    --specpath "$Root\scripts\build" `
    --distpath $DistDir `
    --workpath "$Root\scripts\build" `
    "$Root\scripts\native_host.py"
$BuildExit = $LASTEXITCODE

# PyInstaller refuses to write into a non-empty onedir output and exits non-zero.
# Checking only that the exe exists is not enough: the previous build's exe is
# still sitting there, so the check passes and the run looks successful while the
# old code is what gets shipped. That is how a fixed native_host.py kept serving
# the pre-fix behaviour through a "Built" line.
if ($BuildExit -ne 0) {
    throw "PyInstaller failed with exit code $BuildExit; the exe in $OutDir is the previous build and was not replaced"
}
if (-not (Test-Path $Exe)) { throw "PyInstaller reported success but $Exe is missing" }

# The old onefile exe is still sitting where install_native_host.ps1 used to look,
# and the registry can still point at it. Leaving it is how a stale build keeps
# serving: the file exists, so every existence check passes, and the thing being
# run is not the thing being edited.
$Stale = Join-Path $DistDir "translation-native-host.exe"
if (Test-Path $Stale) {
    Remove-Item $Stale -Force
    Write-Host "Removed stale onefile exe: $Stale"
}

$Stamp = (Get-Item $Exe).LastWriteTime
Write-Host "Built $Exe ($Stamp, $([math]::Round((Get-Item $Exe).Length / 1MB, 1)) MB)"