<#
.SYNOPSIS
    Sample GPU power and utilisation while one replay runs, and record the result.

.DESCRIPTION
    The power saving attributed to the audio path is not the audio path's own: it
    comes from removing whisper's 250ms loop, which held the GPU busy 79% of the
    wall clock. Idle draw is not the state a viewer is in, so the number that
    matters is what the card does while the video plays.

    Samples every second and reports the mean, p95 and maximum of board power and
    of utilisation, plus memory taken two ways: what the compute processes hold,
    and what the whole card reports. Those differ by whatever the desktop and the
    browser hold, and on a 4GB card that difference decides whether a browser at
    fullscreen leaves room for a model.

.EXAMPLE
    .\scripts\measure_power.ps1 -Name pw-whisper250
    .\scripts\measure_power.ps1 -Name pw-audio -AudioNative
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $Name,
    [switch] $AudioNative,
    [switch] $SkipReplay,
    [int] $IntervalSeconds = 1
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot) -ErrorAction SilentlyContinue
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))
$Python = "D:\minanconda\envs\pytorch\python.exe"
$OutDir = "logs\power"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$sampler = {
    param($Path, $Every)
    $samples = @()
    while ($true) {
        $row = & nvidia-smi --query-gpu=timestamp,power.draw,utilization.gpu,memory.used --format=csv,noheader,nounits
        if ($LASTEXITCODE -eq 0 -and $row) {
            $samples += ,$row
            $samples | Set-Content -Path $Path
        }
        Start-Sleep -Seconds $Every
    }
}

$raw = Join-Path $OutDir "$Name.csv"
Remove-Item $raw -ErrorAction SilentlyContinue
$job = Start-Job -ScriptBlock $sampler -ArgumentList $raw, $IntervalSeconds
Write-Host "sampling GPU every ${IntervalSeconds}s -> $raw"

try {
    if (-not $SkipReplay) {
        & .\scripts\replay.ps1 $Name -AudioNative:$AudioNative *>&1 |
            Where-Object { $_ -notmatch "^audio in:|SoundcardRuntimeWarning|warnings\.warn|^\s+\d+\.\d+s " }
    }
}
finally {
    Stop-Job $job -ErrorAction SilentlyContinue
    Receive-Job $job -ErrorAction SilentlyContinue | Out-Null
    Remove-Job $job -Force -ErrorAction SilentlyContinue
}

# Memory is a snapshot: no sample means the card was never asked during the run.
$apps = & nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader
$total = & nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader

& $Python scripts\summarize_power.py $raw $Name --apps $apps --total $total
