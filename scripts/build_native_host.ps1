$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = if ($env:TRANSLATION_PYTHON) { $env:TRANSLATION_PYTHON } else { "D:\minanconda\envs\pytorch\python.exe" }

& $Python -m PyInstaller `
    --clean `
    --onefile `
    --console `
    --name translation-native-host `
    --specpath "$Root\scripts\build" `
    --distpath "$Root\scripts\native-host" `
    --workpath "$Root\scripts\build" `
    "$Root\scripts\native_host.py"

Write-Host "Built $Root\scripts\native-host\translation-native-host.exe"
