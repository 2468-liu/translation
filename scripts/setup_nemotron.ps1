# One-click Nemotron ASR Setup Script for Windows PowerShell
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Write-Host "=== NVIDIA Nemotron 3.5 ASR Windows One-Click Setup ===" -ForegroundColor Cyan

$PythonCmd = "python"
if (Get-Command "python" -ErrorAction SilentlyContinue) {
    $PythonCmd = "python"
} elseif (Test-Path "D:\minanconda\envs\pytorch\python.exe") {
    $PythonCmd = "D:\minanconda\envs\pytorch\python.exe"
}

Push-Location $ProjectRoot
try {
    & $PythonCmd "$ScriptDir\setup_nemotron.py" $args
} finally {
    Pop-Location
}
