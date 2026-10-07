<#
.SYNOPSIS
    Context on or off, crossed with MTP on or off, on one backend.

.DESCRIPTION
    Context injection was switched off because it produced the two failures that
    turn up in every replay: an untranslated English line, and a clause repeated
    until the fragment ends. Turning it off removed both -- three replays, no bad
    fragment -- but it was never the only thing that changed, and MTP arrived in
    the same window.

    The two interact for a reason worth naming. Both change the cost of the same
    stage: context lengthens the prompt, and speculative decoding lengthens
    decode and lengthens prefill with the draft's own pass. If they push on the
    same resource in opposite directions, the measurement of either alone says
    nothing about the pair.

    Four cells, one backend, interleaved, three rounds. One backend because
    2.47.0 answers every audio request with 503 when a draft is loaded, so MTP is
    only expressible on 2.52.0 -- and version must not also be an axis, or the
    result is three variables.

    The backend is therefore pinned to whatever -LlamaVersion names, and the
    resolved version goes into each run's meta. If the pin moves mid-sweep the
    arms stop being comparable and the logs say so.

.EXAMPLE
    .\scripts\sweep_context_mtp.ps1 -LlamaVersion 2.52.0 -Rounds 3
#>
[CmdletBinding()]
param(
    [int] $Rounds = 3,
    [string] $LlamaVersion = "2.52.0",
    [int[]] $ContextValues = @(0, 2),
    [int[]] $GemmaSilence = @(1000),
    [string] $Python = "D:\minanconda\envs\pytorch\python.exe"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot) -ErrorAction SilentlyContinue
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

$env:TRANSLATION_LLAMA_VERSION = $LlamaVersion

function Set-And-Confirm([string] $Env, [string] $Name, [int] $Value) {
    # Assign, then confirm by import.
    #
    # Confirming alone was the bug this replaced: the first cell passed only
    # because the value it asked for happened to be the default, and the second
    # cell threw because nothing had ever set the variable. A sweep that had not
    # thrown would have run all twelve cells with context off and reported a
    # clean 2x2.
    $envItem = Get-Item "Env:$Env" -ErrorAction SilentlyContinue
    Set-Item -Path "Env:$Env" -Value "$Value"
    $seen = & $Python -c "from server import config; print(getattr(config, '$Name'))"
    if ($seen.Trim() -ne "$Value") {
        if ($envItem) { Set-Item -Path "Env:$Env" -Value $envItem.Value }
        else { Remove-Item "Env:$Env" -ErrorAction SilentlyContinue }
        throw "$Name wanted $Value, config reads $($seen.Trim())"
    }
    Write-Host "### $Name = $Value (set via $Env, confirmed by import)"
}

function Start-Backend([switch] $WithDraft) {
    Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    $backend = & .\scripts\resolve_llama.ps1 | Select-Object -Last 1
    $gguf = (Get-ChildItem "models\gemma4-e2b\translate*.gguf" | Select-Object -First 1).FullName
    $mmproj = (Get-ChildItem "models\gemma4-e2b\*mmproj*.gguf" | Select-Object -First 1).FullName
    $a = @(
        "-m", $gguf, "--mmproj", $mmproj, "--mmproj-device", "none",
        "--host", "127.0.0.1", "--port", "8080", "-ngl", "99", "-c", "2048", "-t", "4",
        "--jinja", "-lv", "4"
    )
    $what = "no draft"
    if ($WithDraft) {
        $draft = (Get-ChildItem "models\gemma4-e2b\mtp-*.gguf" -ErrorAction SilentlyContinue |
            Select-Object -First 1).FullName
        if (-not $draft) { throw "no mtp-*.gguf present" }
        $a += @("--model-draft", $draft, "--spec-type", "draft-mtp", "--spec-draft-n-max", "3")
        $what = "MTP draft"
    }
    Write-Host "### llama.cpp $($backend.version), $what"
    Start-Process -FilePath $backend.path -ArgumentList $a -NoNewWindow `
        -RedirectStandardOutput "logs\llama.out" -RedirectStandardError "logs\llama.err"
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
            Write-Host "llama-server ready"
            return
        }
    }
    throw "llama-server did not come up"
}

# Only the two axes under test. Silence is held fixed rather than swept, because
# it was already located and a third axis would make the cell count unmanageable.
foreach ($silence in $GemmaSilence) { Set-And-Confirm "TRANSLATION_GEMMA_MIN_SILENCE_MS" "GEMMA_MIN_SILENCE_MS" $silence }

$cells = @()
foreach ($ctx in $ContextValues) {
    foreach ($mtp in @($false, $true)) {
        $cells += @{
            context = $ctx
            mtp     = $mtp
            name    = "cm-ctx$ctx-mtp$(if ($mtp) { 'on' } else { 'off' })"
        }
    }
}

for ($round = 1; $round -le $Rounds; $round++) {
    foreach ($cell in $cells) {
        $label = "$($cell.name)-r$round"
        Write-Host ""
        Write-Host "===== $label ====="
        Set-And-Confirm "TRANSLATION_AUDIO_CONTEXT_SENTENCES" "AUDIO_CONTEXT_SENTENCES" $cell.context
        try {
            Start-Backend -WithDraft:$cell.mtp
            # Clear the variable so a cell with MTP off cannot inherit it from a
            # previous cell's shell, which is how an arm silently runs the
            # configuration the one before it used.
            $env:TRANSLATION_MTP = if ($cell.mtp) { "1" } else { "" }
            & .\scripts\replay.ps1 $label -AudioNative *>&1 |
                Where-Object { $_ -notmatch "^audio in:|SoundcardRuntimeWarning|warnings\.warn|^\s+\d+\.\d+s |^ASR|^VAD:|^WebSocket|^pipeline:|^server pid|^events=|^client |^video " }
        }
        catch {
            Write-Warning "$label failed: $_"
        }
    }
}

Write-Host ""
Write-Host "===== quality ====="
$names = ($cells | ForEach-Object { $_.name }) -join ","
& $Python scripts\audit_quality.py ($names -split "," | ForEach-Object { "$_-r1" })
