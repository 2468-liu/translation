<#
.SYNOPSIS
    One variable: SETTLE_MS, the caption window's settle pause.

.DESCRIPTION
    The display leg was the only axis the audio route won, and it was reported as
    a single 836 ms. Splitting it did not settle the question either: the hold
    explains the tail but not the median, and the median sits near SETTLE_MS,
    which is 450. Two candidates were left -- the hold and the settle -- and the
    one that moved was the settle.

    Settle is the pause after a one-character revision before the change is
    drawn, so a stream of single-character corrections does not flicker. It is
    also a candidate for swap_deadline, which makes it a latency cost and not only
    a rendering one. Lowering it should shorten the display leg; whether that
    costs legibility is a separate question and the coverage and rewrite counts
    are reported so it can be seen rather than assumed.

    The primary metric is the on-screen one. The package-level number is reported
    too, because they disagreed before: at 1000 ms partials the package showed
    +193 ms while the screen showed -135 ms, since fewer partials means fewer
    rewrites means less time held.

    Everything else is held: the whisper route, 250 ms partials, the same backend.
    No power measurement here -- this is a latency question, and a power trace
    would only add a reason for the card's state to differ between arms.

.EXAMPLE
    .\scripts\sweep_settle.ps1 -Rounds 3
#>
[CmdletBinding()]
param(
    [int] $Rounds = 3,
    [int[]] $SettleValues = @(450, 150),
    [int] $PartialIntervalMs = 250,
    [string] $Python = "D:\minanconda\envs\pytorch\python.exe"
)

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

function Set-Threshold([string] $Env, [string] $Check, [int] $Value) {
    # Assign, then confirm by import.
    #
    # Confirming alone was a real bug in another sweep: the first cell passed
    # because the value it wanted was already the default, and the second threw
    # because nothing had ever set the variable -- so the sweep was about to run
    # every cell with the setting off and report a clean result.
    $previous = Get-Item "Env:$Env" -ErrorAction SilentlyContinue
    Set-Item -Path "Env:$Env" -Value "$Value"
    $seen = & $Python -c "from server import config; print(getattr(config, '$Check'))"
    if ($seen.Trim() -ne "$Value") {
        if ($previous) { Set-Item -Path "Env:$Env" -Value $previous.Value }
        else { Remove-Item "Env:$Env" -ErrorAction SilentlyContinue }
        throw "$Check wanted $Value, config reads $($seen.Trim())"
    }
    Write-Host "### $Check = $Value (set via $Env, confirmed by import)"
}

# caption_window reads SETTLE_MS from its own module, so the check imports that
# module rather than config -- confirming config would prove nothing about the
# process that actually draws.
function Set-Settle([int] $Value) {
    $previous = Get-Item "Env:TRANSLATION_SETTLE_MS" -ErrorAction SilentlyContinue
    Set-Item -Path "Env:TRANSLATION_SETTLE_MS" -Value "$Value"
    $seen = & $Python -c "from server import caption_window; print(caption_window.SETTLE_MS)"
    if ($seen.Trim() -ne "$Value") {
        if ($previous) { Set-Item -Path "Env:TRANSLATION_SETTLE_MS" -Value $previous.Value }
        else { Remove-Item "Env:TRANSLATION_SETTLE_MS" -ErrorAction SilentlyContinue }
        throw "SETTLE_MS wanted $Value, caption_window reads $($seen.Trim())"
    }
    Write-Host "### SETTLE_MS = $Value (set via TRANSLATION_SETTLE_MS, confirmed by caption_window)"
}

Set-Threshold "TRANSLATION_PARTIAL_INTERVAL_MS" "PARTIAL_INTERVAL_MS" $PartialIntervalMs

# The whisper route serves its translation model from llama-server on 8080 and
# replay.ps1 deliberately leaves that server alone, so it has to be started here
# or every arm records a card that translated nothing.
if (-not (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)) {
    $Llama = (& .\scripts\resolve_llama.ps1 | Select-Object -Last 1).path
    $gguf = (Get-ChildItem "models\hy-mt2-1.8b\*.gguf" | Select-Object -First 1).FullName
    Write-Host "starting llama-server ($([System.IO.Path]::GetFileName($gguf)))"
    Start-Process -FilePath $Llama -ArgumentList @(
        "-m", $gguf, "--host", "127.0.0.1", "--port", "8080",
        "-ngl", "99", "-c", "2048", "-t", "4", "-np", "1", "-lv", "4"
    ) -NoNewWindow -RedirectStandardOutput "logs\llama.out" -RedirectStandardError "logs\llama.err"
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) { break }
    }
}

# Interleaved. Settle and the thermal state of the card are exactly the kind of
# difference that one block per value would mistake for each other.
for ($round = 1; $round -le $Rounds; $round++) {
    foreach ($settle in $SettleValues) {
        $label = "settle$settle-r$round"
        Write-Host ""
        Write-Host "===== $label ====="
        try {
            Set-Settle $settle
            & .\scripts\replay.ps1 $label *>&1 |
                Where-Object { $_ -notmatch "^audio in:|SoundcardRuntimeWarning|warnings\.warn|^\s+\d+\.\d+s |^ASR|^VAD:|^WebSocket|^pipeline:|^server pid|^events=|^client |^video |^stopping|^archived|^llama-server ready|^waiting for|^llama.cpp win" }
            & $Python scripts\manifest.py check $label
        }
        catch {
            Write-Warning "$label failed: $_"
        }
    }
}

Write-Host ""
Write-Host "===== manifest, including rejected arms ====="
& $Python scripts\manifest.py sweep settle

Write-Host ""
Write-Host "===== display leg, on-screen first character ====="
& $Python scripts\probe_reference.py (@(
    foreach ($settle in $SettleValues) {
        "settle$settle=" + ((1..$Rounds | ForEach-Object { "settle$settle-r$_" }) -join ",")
    }
) + "--allow-unmanifested")

Write-Host ""
Write-Host "===== what the deadline was waiting for ====="
& $Python scripts\probe_deadline.py (@(
    foreach ($settle in $SettleValues) {
        "settle$settle=" + ((1..$Rounds | ForEach-Object { "settle$settle-r$_" }) -join ",")
    }
))