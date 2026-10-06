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
    $next = [regex]::Replace($text, "PARTIAL_INTERVAL_MS = \d+", "PARTIAL_INTERVAL_MS = $Ms")
    if ($next -eq $text) { throw "PARTIAL_INTERVAL_MS not found in $Config" }
    Set-Content $Config -Value $next -Encoding utf8 -NoNewline
    $seen = [regex]::Match((Get-Content $Config -Raw), "PARTIAL_INTERVAL_MS = (\d+)").Groups[1].Value
    if ($seen -ne "$Ms") { throw "wanted $Ms, config reads $seen" }
    Write-Host ""
    Write-Host "### PARTIAL_INTERVAL_MS = $Ms (confirmed)"
}

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
