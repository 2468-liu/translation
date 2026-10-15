<#
.SYNOPSIS
    The real recording, on both routes, with the manifest recording everything.

.DESCRIPTION
    The TTS clip hides a whole code path. It has eighteen pauses of a second or
    more; the meeting recording has none. GEMMA_MIN_SILENCE_MS is 1000, so every
    number this project has about that threshold was measured on a clip that
    triggers it constantly, and a continuous conversation never triggers it at
    all -- the audio-native route then depends entirely on the length caps, and
    its first character lands roughly a segment length after the speaker starts.

    Both routes run, because whisper + hy-mt2 is the default and the audio arm
    only matters as the alternative to it. Interleaved, so neither gets the
    machine's warm state.

    This is observation: one variable would be the clip, and both routes see the
    same clip under the same thresholds. Nothing here changes a setting.

.EXAMPLE
    .\scripts\sweep_real.ps1 -Rounds 2
#>
[CmdletBinding()]
param(
    [int] $Rounds = 2,
    [string] $Python = "D:\minanconda\envs\pytorch\python.exe",
    [string[]] $Routes = @("whisper", "audio")
)

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

# Both routes read the same clip and the same sentence timeline, so every latency
# number is measured against the same speech. If the two disagreed the zero would
# be wrong for whichever arm was measured against the wrong one.
$env:TRANSLATION_SAMPLE_WAV = "$PWD\samples\meeting_6min.wav"
$env:TRANSLATION_SAMPLE_TIMING = "$PWD\samples\meeting_6min_timing.json"

function Start-HyMt2 {
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
        if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) { return }
    }
    throw "llama-server did not come up on 8080"
}

Write-Host "### clip: samples\meeting_6min.wav  timeline: samples\meeting_6min_timing.json"
Write-Host "### holds: partial 250ms, silence 1000ms, context 0, MTP off"

for ($round = 1; $round -le $Rounds; $round++) {
    foreach ($route in $Routes) {
        $label = "real-$route-r$round"
        Write-Host ""
        Write-Host "===== $label ====="
        try {
            if ($route -eq "whisper") {
                $env:TRANSLATION_AUDIO_NATIVE = "0"
                Start-HyMt2
                & .\scripts\replay.ps1 $label *>&1 |
                    Where-Object { $_ -notmatch "^audio in:|SoundcardRuntimeWarning|warnings\.warn|^\s+\d+\.\d+s |^ASR|^VAD:|^WebSocket|^pipeline:|^server pid|^events=|^client |^video |^stopping|^archived|^llama-server ready|^waiting for|^llama.cpp win" }
            }
            else {
                $env:TRANSLATION_AUDIO_NATIVE = "1"
                $env:TRANSLATION_EXPECT_DRAFT = "0"
                & .\scripts\replay.ps1 $label -AudioNative -KeepBackend *>&1 |
                    Where-Object { $_ -notmatch "^audio in:|SoundcardRuntimeWarning|warnings\.warn|^\s+\d+\.\d+s |^ASR|^VAD:|^WebSocket|^pipeline:|^server pid|^events=|^client |^video |^stopping|^archived|^llama-server ready|^waiting for|^llama.cpp win" }
            }
            & $Python scripts\manifest.py check $label
        }
        catch {
            # The whole exception, not $_.Exception.Message.
            #
            # Write-Warning "$_" prints the message alone, which for a Python
            # subprocess is "exit 1" and for a PowerShell throw is the first line.
            # The last sweep lost four cells to an exception whose cause was never
            # visible, and the cause turned out not to be the one that had been
            # assumed in the writeup. A guard that hides its own failure is worse
            # than no guard.
            Write-Warning "$label failed:"
            Write-Warning ($_.ScriptStackTrace)
            Write-Warning ($_.Exception | Out-String)
            if ($_.Exception.InnerException) {
                Write-Warning "inner:"
                Write-Warning ($_.Exception.InnerException | Out-String)
            }
        }
    }
}

Write-Host ""
Write-Host "===== segment structure: how the audio was cut ====="
& $Python scripts\audit_segments.py @(
    foreach ($route in $Routes) {
        "real-$route=" + ((1..$Rounds | ForEach-Object { "real-$route-r$_" }) -join ",")
    }
) --allow-unmanifested

Write-Host ""
Write-Host "===== latency, same zero (the meeting timeline) ====="
& $Python scripts\probe_reference.py @(
    foreach ($route in $Routes) {
        "real-$route=" + ((1..$Rounds | ForEach-Object { "real-$route-r$_" }) -join ",")
    }
) --allow-unmanifested

Write-Host ""
Write-Host "===== manifest ledger ====="
& $Python scripts\manifest.py sweep real