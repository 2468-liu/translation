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
    # Rounds per arm, interleaved rather than run to completion one after
    # another. One replay of this material has been seen to differ from itself by
    # a factor of two, so one round is a sample rather than a measurement.
    [int] $Rounds = 2,
    [string] $Python = "D:\minanconda\envs\pytorch\python.exe"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot) -ErrorAction SilentlyContinue
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

function Start-IdleModel([string] $Which) {
    Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    if ($Which -eq "hy") { Ensure-TranslationModel; return }
    $Llama = (& .\scripts\resolve_llama.ps1 | Select-Object -Last 1).path
    $gguf = (Get-ChildItem "models\gemma4-e2b\translate*.gguf" | Select-Object -First 1).FullName
    $mmproj = (Get-ChildItem "models\gemma4-e2b\*mmproj*.gguf" | Select-Object -First 1).FullName
    Write-Host "starting llama-server (gemma audio model, projector on CPU, idle)"
    Start-Process -FilePath $Llama -ArgumentList @(
        "-m", $gguf, "--mmproj", $mmproj, "--mmproj-device", "none",
        "--host", "127.0.0.1", "--port", "8080", "-ngl", "99", "-c", "2048", "-t", "4", "--jinja", "-lv", "4"
    ) -NoNewWindow -RedirectStandardOutput "logs\llama.out" -RedirectStandardError "logs\llama.err"
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
            Write-Host "llama-server ready"
            return
        }
    }
    throw "llama-server did not come up for the idle-model baseline"
}

function Set-GemmaSilence([int] $Ms) {
    $env:TRANSLATION_GEMMA_MIN_SILENCE_MS = "$Ms"
    $seen = & $Python -c "from server.config import GEMMA_MIN_SILENCE_MS; print(GEMMA_MIN_SILENCE_MS)"
    if ($seen.Trim() -ne "$Ms") { throw "wanted $Ms, config reads $($seen.Trim())" }
    Write-Host "### GEMMA_MIN_SILENCE_MS = $Ms (confirmed by import)"
}

function Set-PartialInterval([int] $Ms) {
    # Injected, not written into config.py.
    #
    # Rewriting the file made the working tree dirty, so every run after the
    # first was stamped "+dirty" and no two runs could be compared; it also broke
    # the vad tests that read these constants while a sweep was in flight, and
    # nothing recorded which arm a run belonged to beyond the run's name.
    $env:TRANSLATION_PARTIAL_INTERVAL_MS = "$Ms"
    $seen = & $Python -c "from server.config import PARTIAL_INTERVAL_MS; print(PARTIAL_INTERVAL_MS)"
    if ($seen.Trim() -ne "$Ms") { throw "wanted $Ms, config reads $($seen.Trim())" }
    Write-Host ""
    Write-Host "### PARTIAL_INTERVAL_MS = $Ms (confirmed by import)"
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
    $Llama = (& .\scripts\resolve_llama.ps1 | Select-Object -Last 1).path
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

# The arms, in the order they run. Interleaved on purpose: running each
# configuration to completion before starting the next lets the card's thermal
# state drift with the order, and a difference between two arms then contains
# however warm the second one was. Round-robin puts the drift in shared
# comparison instead of in one arm.
$arms = @(
    @{ name = "pw-baseline-video"; kind = "baseline" }
    @{ name = "pw-baseline-hymt"; kind = "baseline-model"; model = "hy" }
    @{ name = "pw-baseline-e2b"; kind = "baseline-model"; model = "gemma" }
    @{ name = "pw-whisper250"; kind = "whisper"; partial = 250 }
    @{ name = "pw-audio-a"; kind = "audio" }
    @{ name = "pw-whisper500"; kind = "whisper"; partial = 500 }
    @{ name = "pw-whisper750"; kind = "whisper"; partial = 750 }
    @{ name = "pw-whisper-closed"; kind = "whisper"; partial = 0 }
    @{ name = "pw-whisper1000"; kind = "whisper"; partial = 1000 }
)

for ($round = 1; $round -le $Rounds; $round++) {
    foreach ($arm in $arms) {
        $label = if ($Rounds -eq 1) { $arm.name } else { "$($arm.name)-r$round" }
        Write-Host ""
        Write-Host "===== $label (round $round/$Rounds) ====="
        try {
            if ($arm.kind -eq "baseline") {
                # Video only: no caption service, nothing on 8080. This is the
                # floor every other arm sits on top of -- the decoder, the
                # compositor and the panel are all drawing whatever they draw.
                Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
                & .\scripts\measure_power.ps1 -Name $label -BaselineSeconds 200
            }
            elseif ($arm.kind -eq "baseline-model") {
                # One model resident and idle. Split from the video-only floor
                # because every other arm runs with a model loaded, and a model
                # that is only resident still draws its idle power -- about
                # 10.6W here. Measuring it separately is what lets each arm be
                # read as an increment rather than as a total.
                Start-IdleModel -Which $arm.model
                & .\scripts\measure_power.ps1 -Name $label -BaselineSeconds 200
            }
            else {
                if ($arm.kind -eq "whisper") { Set-PartialInterval $arm.partial }
                & .\scripts\measure_power.ps1 -Name $label -AudioNative:($arm.kind -eq "audio")
            }
        }
        catch {
            Write-Warning "$label failed: $_"
        }
    }
}
