<#
.SYNOPSIS
    Long-running daemon process that hosts llama-server on 8080 (Index-Translate) and 8099 (Qwen3-ASR).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

. (Join-Path $PSScriptRoot "probes.ps1")

# 1. Resolve llama executable
$backend = & (Join-Path $PSScriptRoot "resolve_llama.ps1") | Select-Object -Last 1
if (-not $backend) {
    throw "llama engine not found! Please check PATH, LM Studio backends, or tools/."
}
$LlamaExe = $backend.path
$isCli = ($backend.is_cli -eq $true) -or ((Split-Path $LlamaExe -Leaf) -match "^llama(\.exe)?$")

# 2. Resolve python interpreter
$Python = if ($env:TRANSLATION_PYTHON -and (Test-Path $env:TRANSLATION_PYTHON)) {
    $env:TRANSLATION_PYTHON
} else {
    $foundPy = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($foundPy) { $foundPy.Source } else { "D:\minanconda\envs\pytorch\python.exe" }
}

$indexGguf = (Get-IndexTranslateGguf).FullName
$qwenPaths = & $Python -c "from server.config import QWEN3_ASR_MODEL, QWEN3_ASR_MMPROJ; print(QWEN3_ASR_MODEL); print(QWEN3_ASR_MMPROJ)"
$qwenModel = $qwenPaths[0]
$qwenMmproj = $qwenPaths[1]

$LogDir = Join-Path $Root "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

Write-Host "daemon_llama: Starting 8080 Index-Translate ($(Split-Path $LlamaExe -Leaf))..."
$args80 = if ($isCli) { @("serve") } else { @() }
$args80 += @(
    "-m", $indexGguf, "--host", "127.0.0.1", "--port", "8080",
    "-ngl", "60", "-c", "2048", "-t", "4", "-np", "3", "--no-webui", "-lv", "4", "--reasoning", "off"
)
$p80 = Start-Process -FilePath $LlamaExe -WorkingDirectory $Root -ArgumentList $args80 `
    -RedirectStandardOutput (Join-Path $LogDir "llama.out") -RedirectStandardError (Join-Path $LogDir "llama.err") -WindowStyle Hidden -PassThru

Write-Host "daemon_llama: Starting 8099 Qwen3-ASR ($(Split-Path $LlamaExe -Leaf))..."
$args99 = if ($isCli) { @("serve") } else { @() }
$args99 += @(
    "-m", $qwenModel, "--mmproj", $qwenMmproj, "--host", "127.0.0.1", "--port", "8099",
    "-ngl", "99", "-c", "2048", "-t", "4", "-np", "4"
)
$p99 = Start-Process -FilePath $LlamaExe -WorkingDirectory $Root -ArgumentList $args99 `
    -RedirectStandardOutput (Join-Path $LogDir "llama8099.out") -RedirectStandardError (Join-Path $LogDir "llama8099.err") -WindowStyle Hidden -PassThru

Write-Host "daemon_llama: Waiting for health check..."
$ready80 = $false
$ready99 = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    if (-not $ready80) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8080/health" -TimeoutSec 1 -UseBasicParsing -ErrorAction SilentlyContinue
            if ($r.StatusCode -eq 200) { $ready80 = $true }
        } catch {}
    }
    if (-not $ready99) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:8099/health" -TimeoutSec 1 -UseBasicParsing -ErrorAction SilentlyContinue
            if ($r.StatusCode -eq 200) { $ready99 = $true }
        } catch {}
    }
    if ($ready80 -and $ready99) {
        Write-Host "daemon_llama: READY -- Both 8080 and 8099 are healthy and serving!"
        break
    }
}

if (-not $ready80 -or -not $ready99) {
    throw "daemon_llama: Backends failed to become healthy within 40 seconds (8080=$ready80, 8099=$ready99)"
}

# Keep running as long as both processes are alive
while (-not $p80.HasExited -and -not $p99.HasExited) {
    Start-Sleep -Seconds 5
}
Write-Host "daemon_llama: One or more backend processes exited, daemon terminating."
