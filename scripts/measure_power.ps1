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
    [int] $IntervalSeconds = 1,
    # Samples before the run is treated as settled. The first seconds of a replay
    # are the model warming up and the card clocking up, and they belong to no
    # configuration.
    [int] $WarmupSeconds = 30,
    # Play the material for this long with no caption service at all, as the
    # floor the other arms sit on. ffmpeg decodes to a null sink: the video
    # pipeline and the display work, nothing is recognised or translated.
    [int] $BaselineSeconds = 0
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot) -ErrorAction SilentlyContinue
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))
$Python = "D:\minanconda\envs\pytorch\python.exe"
$OutDir = "logs\power"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$sampler = {
    param($Path, $Every, $CpuPath)
    # Absolute, and the directory created here rather than assumed: a background
    # job does not inherit this script's working directory, so a relative path
    # lands somewhere else and Set-Content fails on a missing directory -- which
    # is invisible unless the job's errors are read.
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $lines = New-Object System.Collections.Generic.List[string]
    $cpu = New-Object System.Collections.Generic.List[string]
    while ($true) {
        $row = & nvidia-smi --query-gpu=timestamp,power.draw,utilization.gpu,memory.used,temperature.gpu,clocks.sm --format=csv,noheader,nounits
        if ($LASTEXITCODE -eq 0 -and $row) {
            $lines.Add(($row -join " "))
            Set-Content -Path $Path -Value $lines
        }
        # CPU alongside the GPU, not instead of it.
        #
        # The audio path puts the projector on the CPU, so its GPU-only figure
        # flatters it: work that moved off the card still costs the machine, and
        # the two share a cooling path on a laptop. typeperf needs no counter
        # names and works unprivileged.
        try {
            $proc = (Get-Counter "\Process(*)\% Processor Time" -ErrorAction Stop).CounterSamples
            $busy = ($proc | Where-Object { $_.InstanceName -notmatch "^(_total|Idle)" } |
                Measure-Object -Property CookedValue -Sum).Sum
            $cpu.Add(("{0:N1}" -f ($busy / [Environment]::ProcessorCount)))
            Set-Content -Path $CpuPath -Value $cpu
        }
        catch {
            # Recorded once as an empty file so the report can say the CPU column
            # was wanted and not had, rather than omitting it silently.
            if (-not (Test-Path $CpuPath)) { Set-Content -Path $CpuPath -Value "" }
        }
        Start-Sleep -Seconds $Every
    }
}

$raw = Join-Path (Resolve-Path $OutDir) "$Name.csv"
$cpuRaw = Join-Path (Resolve-Path $OutDir) "$Name.cpu.txt"
Remove-Item $raw, $cpuRaw -ErrorAction SilentlyContinue
$job = Start-Job -ScriptBlock $sampler -ArgumentList $raw, $IntervalSeconds, $cpuRaw
Write-Host "sampling GPU every ${IntervalSeconds}s -> $raw  (cpu -> $cpuRaw)"

try {
    if ($BaselineSeconds -gt 0) {
        $ffmpeg = (Get-ChildItem "tools\ffmpeg\ffmpeg-*\bin\ffmpeg.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1).FullName
        if (-not $ffmpeg) { throw "ffmpeg not found for the baseline arm" }
        Write-Host "baseline: decoding samples/blog_3min.mp4 to a null sink for ${BaselineSeconds}s"
        & $ffmpeg -hide_banner -loglevel error -re -stream_loop -1 -i "samples\blog_3min.mp4" -t $BaselineSeconds -f null -
    }
    elseif (-not $SkipReplay) {
        & .\scripts\replay.ps1 $Name -AudioNative:$AudioNative *>&1 |
            Where-Object { $_ -notmatch "^audio in:|SoundcardRuntimeWarning|warnings\.warn|^\s+\d+\.\d+s " }
    }
}
finally {
    Stop-Job $job -ErrorAction SilentlyContinue
    # Read the job's errors rather than discarding them. A sampler that wrote
    # nothing looks exactly like a machine that drew no power.
    $problems = Receive-Job $job -ErrorAction SilentlyContinue 2>&1 |
        Where-Object { $_ -is [System.Management.Automation.ErrorRecord] }
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    if ($problems) {
        Write-Warning "sampler reported: $($problems[0])"
    }
    if (-not (Test-Path $raw)) {
        throw "no trace at $raw -- the sampler produced nothing, so this run's power is unknown"
    }
}

# Memory is a snapshot: no sample means the card was never asked during the run.
$apps = & nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader
$total = & nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader

& $Python scripts\summarize_power.py $raw $Name --apps $apps --total $total --warmup $WarmupSeconds --cpu $cpuRaw
