<#
.SYNOPSIS
    Trace GPU power across both pipelines on the same material.

.DESCRIPTION
    The saving attributed to the audio path came from removing whisper's 250ms
    loop, which held the card 79% busy. That does not make the audio path the
    reason -- slowing whisper's loop down would claim part of the same saving.
    The only way to tell is to put both curves on one axis.

    Four whisper settings and one audio setting, one replay each, sampled once a
    second while the video plays. Idle draw is not the state a viewer is in.

    The whisper partial interval is written before each run and read back, for
    the same reason the silence sweep does it: a run that quietly inherited the
    previous setting would look exactly like a setting that changed nothing.

.EXAMPLE
    .\scripts\sweep_power.ps1
#>
[CmdletBinding()]
param(
    [int[]] $PartialIntervals = @(250, 500, 1000, 0),
    [string] $Config = "server\config.py"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot) -ErrorAction SilentlyContinue
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

function Set-PartialInterval([int] $Ms) {
    $text = Get-Content $Config -Raw
    $pattern = "PARTIAL_INTERVAL_MS = \d+"
    # Match, not "the text changed". Asking whether the file differs reports a
    # miss whenever the requested value is already the current one, which is the
    # first level of every sweep and stops the run before it starts.
    if (-not [regex]::IsMatch($text, $pattern)) { throw "PARTIAL_INTERVAL_MS not found in $Config" }
    Set-Content $Config -Value ([regex]::Replace($text, $pattern, "PARTIAL_INTERVAL_MS = $Ms")) -Encoding utf8 -NoNewline
    $seen = [regex]::Match((Get-Content $Config -Raw), "PARTIAL_INTERVAL_MS = (\d+)").Groups[1].Value
    if ($seen -ne "$Ms") { throw "wanted $Ms, config reads $seen" }
    Write-Host ""
    Write-Host "### PARTIAL_INTERVAL_MS = $Ms (confirmed)"
}

function Ensure-TranslationModel {
    # The whisper route serves its translation model from llama-server on 8080,
    # and replay.ps1 leaves that server alone so it stays warm between runs. It
    # therefore has to be started here or every whisper arm records a card that
    # never translated anything -- which is exactly what happened once already.
    if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "llama-server already on 8080"
        return
    }
    $Llama = "C:\Users\20693\.lmstudio\extensions\backends\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.47.0\llama-server.exe"
    $gguf = (Get-ChildItem "models\hy-mt2-1.8b\*.gguf" | Select-Object -First 1).FullName
    Write-Host "starting llama-server ($([System.IO.Path]::GetFileName($gguf)))"
    Start-Process -FilePath $Llama -ArgumentList @(
        "-m", $gguf, "--host", "127.0.0.1", "--port", "8080",
        "-ngl", "99", "-c", "2048", "-t", "4", "-np", "1", "-lv", "4"
    ) -NoNewWindow -RedirectStandardOutput "logs\llama.out" -RedirectStandardError "logs\llama.err"
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
            Write-Host "llama-server ready"
            return
        }
        Write-Host "  waiting for llama-server ($((($i + 1) * 5))s)"
    }
    throw "llama-server did not come up; the whisper arms would translate nothing"
}

Ensure-TranslationModel

foreach ($ms in $PartialIntervals) {
    Set-PartialInterval $ms
    $label = if ($ms -eq 0) { "pw-whisper-closed" } else { "pw-whisper$ms" }
    Write-Host ""
    Write-Host "===== $label ====="
    try {
        & .\scripts\measure_power.ps1 -Name $label
    }
    catch {
        Write-Warning "$label failed: $_"
    }
}

Write-Host ""
Write-Host "===== pw-audio ====="
try {
    & .\scripts\measure_power.ps1 -Name "pw-audio" -AudioNative
}
catch {
    Write-Warning "pw-audio failed: $_"
}
