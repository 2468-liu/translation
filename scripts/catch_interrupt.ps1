<#
.SYNOPSIS
    Catch an interrupted replay with the diagnostic that says which side died.

.DESCRIPTION
    Six replays so far, four complete and two stopped at "no close frame received or sent"
    (B253-fix at 48.6s, B256 at 34.4s). Both failures were on the qwen3 arm and both
    complete qwen3 runs were on the same arm, while two whisper runs finished. That is
    four samples and it is not a result, but it is enough to decide where to spend the
    next run: qwen3 is the only arm that produces the 30-33 second repetition, so if the
    interruption is something about that response the failures should cluster there.

    The diagnostic itself (replay.ps1, "after playback: server alive=...") was added
    after both failures, so neither of them recorded which side went away. This loops the
    qwen3 arm until one does, and stops. Each iteration is a full ~4 minute replay, so four
    is the budget and the expected yield is one or two failures in that.

    The first version of this file opened with a Python-style triple-quoted docstring,
    which PowerShell parses as code -- the second quote of the first sentence starts a
    string and the third ends it, leaving a bare `no` token, so the script never ran and
    reported a syntax error on line 3. PowerShell's block comment is the hash-bracket
    form, as everywhere else in this repository.

    The fix for that then failed too, for a related reason: the replacement comment
    explained the mistake by *naming* the two delimiters, and a block comment nests, so
    the opening delimiter inside it opened a second level and the following closing one
    ended that level instead. The delimiters must not appear inside the text -- that is
    why the line above describes them instead of writing them.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\catch_interrupt.ps1
#>

$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
$MaxRuns = 4

$env:TRANSLATION_MEASUREMENT_LLAMA_VERSION = "2.53.0"
$env:TRANSLATION_SAMPLE_WAV = "samples\lecture_mix.wav"
$env:TRANSLATION_SAMPLE_TIMING = "samples\lecture_mix_timing.json"
$env:TRANSLATION_ASR_BACKEND = "qwen3"
$env:TRANSLATION_PARTIAL_INTERVAL_MS = "750"

for ($i = 1; $i -le $MaxRuns; $i++) {
    $name = "CATCH$i"
    $out = "logs\$name.out"
    Write-Host "=== $name（第 $i 次 / 共 $MaxRuns 次）=== $(Get-Date -Format HH:mm:ss)"

    & powershell -NoProfile -ExecutionPolicy Bypass -File scripts\replay.ps1 $name *> $out

    $raw = [System.IO.File]::ReadAllBytes($out)
    $text = if ($raw.Length -ge 2 -and $raw[0] -eq 0xFF -and $raw[1] -eq 0xFE) {
        [System.Text.Encoding]::Unicode.GetString($raw)
    } else {
        [System.Text.Encoding]::UTF8.GetString($raw)
    }

    $stopped = ($text -split "`r?`n") | Where-Object { $_ -match "play stopped" } | Select-Object -First 1
    $after = ($text -split "`r?`n") | Where-Object { $_ -match "after playback" } | Select-Object -First 1

    if ($stopped) {
        Write-Host "  抓到了：$($stopped.Trim())"
        if ($after) { Write-Host "  $($after.Trim())" }
        Write-Host "  完整输出：$out"
        break
    }
    if ($after) {
        Write-Host "  这次跑完了：$($after.Trim())"
    } else {
        Write-Host "  这次没跑完，但也没有 play stopped —— 看 $out"
    }
    if ($i -lt $MaxRuns) { Start-Sleep -Seconds 5 }
}