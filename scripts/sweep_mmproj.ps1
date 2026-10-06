<#
.SYNOPSIS
    One variable changed: the projector moves from the CPU to the GPU.

.DESCRIPTION
    Every Gemma measurement so far ran with --mmproj-device none, which the help
    text defines as not offloading the projector at all. It was set because the
    card was believed to be at 3774 of 4096 MiB with 320 to spare, and that
    figure was wrong by half: nvidia-smi reads 1842, and the projector is
    532 MiB. On the GPU it would sit at roughly 2374 of 4096.

    That decision shapes the numbers rather than just their memory cost. The
    projector is the audio encoder, and with it on the CPU the prefill is partly
    CPU work -- which is why CPU sampling became a blocking requirement for the
    power comparison, and why an earlier argument that speculative decoding
    "cannot help because prefill is CPU-bound" was never actually measured.

    So the whole Gemma set is provisional, not wrong: it is a set measured with
    the load in a particular place. This arm re-measures it with the load on the
    GPU, changing nothing else -- same backend, no draft, no context, same
    closing silence. One variable, so the difference is attributable.

.EXAMPLE
    .\scripts\sweep_mmproj.ps1 -Rounds 3
#>
[CmdletBinding()]
param(
    [int] $Rounds = 3,
    # Backend held at whatever this names, so the comparison is with the sweep
    # that has already run rather than with a different build as well.
    [string] $LlamaVersion = "2.47.0",
    [string[]] $Devices = @("none", "cuda0"),
    [string] $Python = "D:\minanconda\envs\pytorch\python.exe"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot) -ErrorAction SilentlyContinue
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

$env:TRANSLATION_LLAMA_VERSION = $LlamaVersion
# The other two axes are held at their current values and stated here rather than
# left implicit, so the arm cannot be read as "the audio arm" by accident.
$env:TRANSLATION_AUDIO_CONTEXT_SENTENCES = "0"
$env:TRANSLATION_GEMMA_MIN_SILENCE_MS = "1000"
$env:TRANSLATION_MTP = ""

$seen = & $Python -c "from server import config; print(config.AUDIO_CONTEXT_SENTENCES, config.GEMMA_MIN_SILENCE_MS)"
Write-Host "### held fixed: context=$($seen.Split(' ')[0]) silence=$($seen.Split(' ')[1]) mtp=off  llama=$LlamaVersion"

# Interleaved: the projector placement is exactly the kind of difference that a
# card's thermal state would otherwise be mistaken for.
for ($round = 1; $round -le $Rounds; $round++) {
    foreach ($device in $Devices) {
        $tag = if ($device -eq "none") { "cpu" } else { "gpu" }
        $label = "mmproj-$tag-r$round"
        Write-Host ""
        Write-Host "===== $label (llama $LlamaVersion, projector on $tag) ====="
        $env:TRANSLATION_MMPROJ_DEVICE = $device
        try {
            & .\scripts\replay.ps1 $label -AudioNative *>&1 |
                Where-Object { $_ -notmatch "^audio in:|SoundcardRuntimeWarning|warnings\.warn|^\s+\d+\.\d+s |^ASR|^VAD:|^WebSocket|^pipeline:|^server pid|^events=|^client |^video |^stopping|^archived" }
            & nvidia-smi --query-gpu=memory.used --format=csv,noheader | ForEach-Object { "    card memory: $_" }
        }
        catch {
            Write-Warning "$label failed: $_"
        }
    }
}

Write-Host ""
Write-Host "===== latency, same zero ====="
$runs = @()
for ($round = 1; $round -le $Rounds; $round++) {
    foreach ($device in $Devices) {
        $tag = if ($device -eq "none") { "cpu" } else { "gpu" }
        $runs += "$tag-r$round=mmproj-$tag-r$round"
    }
}
$groups = @()
foreach ($device in $Devices) {
    $tag = if ($device -eq "none") { "cpu" } else { "gpu" }
    $names = (1..$Rounds | ForEach-Object { "mmproj-$tag-r$_" }) -join ","
    $groups += "projector-$tag=$names"
}
& $Python scripts\probe_reference.py @groups

Write-Host ""
Write-Host "===== quality ====="
foreach ($device in $Devices) {
    $tag = if ($device -eq "none") { "cpu" } else { "gpu" }
    $names = (1..$Rounds | ForEach-Object { "mmproj-$tag-r$_" }) -join ","
    & $Python scripts\audit_quality.py ($names -split ",")
}
