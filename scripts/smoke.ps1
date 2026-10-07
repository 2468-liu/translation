<#
.SYNOPSIS
    One minute, end to end, on ten seconds of audio.

.DESCRIPTION
    Run this after any change to the replay or archiving machinery, before any
    sweep. Two separate twelve-run losses came from editing that machinery and
    finding out during a sweep: twelve cells labelled "MTP on" that all ran with
    MTP off because replay.ps1 killed the draft backend, and four cells lost to an
    exception whose message was swallowed by the catch block that reported it. In
    both cases a one-minute check would have caught it, and in neither case was
    one run.

    What it asserts, in order:
      the server comes up
      the clip plays to completion
      translations are produced
      the logs are archived under this run's own name
      the manifest for it either passes or fails for a reason a human wrote

    A smoke test that only checks "did it crash" would have passed both times.
    The failures were not crashes; they were runs that completed and reported the
    wrong thing.

.EXAMPLE
    .\scripts\smoke.ps1
#>
[CmdletBinding()]
param(
    [switch] $AudioNative,
    [string] $Python = "D:\minanconda\envs\pytorch\python.exe"
)

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

$name = "smoke10s"
$wav = "$PWD\samples\smoke_10s.wav"
if (-not (Test-Path $wav)) { throw "smoke clip missing: $wav (make it from a real recording first)" }
$env:TRANSLATION_SAMPLE_WAV = $wav
$env:TRANSLATION_SAMPLE_TIMING = "$PWD\samples\smoke_10s_timing.json"

function Check([string] $What, [bool] $Ok, [string] $Detail = "") {
    if ($Ok) { Write-Host "  ok    $What" }
    else { Write-Host "  FAIL  $What  $Detail"; $script:bad = $true }
}
$bad = $false

# Static check first: a loop variable that shares a name with a parameter.
#
# PowerShell variables are case-insensitive, so `foreach ($name in ...)` inside a
# function whose parameter is `$Name` silently rewrites the parameter. That
# happened here: the quarantine loop was added with $name, and from then on every
# archive went to "perf.blog_3min_test.json.log" while the meta recorded the
# filename as the arm. Reading the file does not show it; running it does. So it
# is checked here instead of being left to a reviewer's eye.
foreach ($itScript in Get-ChildItem "scripts\*.ps1") {
    # From the parser, not from a pattern. Both regex attempts were wrong: a
    # pattern loose enough to find the parameter block also spans function calls,
    # and a pattern tight enough not to flags nothing. The AST is the same object
    # PowerShell itself uses, so there is nothing left to guess.
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $itScript.FullName, [ref] $tokens, [ref] $parseErrors)
    if (-not $ast.ParamBlock) { continue }
    $paramNames = $ast.ParamBlock.Parameters |
        ForEach-Object { $_.Name.VariablePath.UserPath }
    $loops = $ast.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.ForEachStatementAst] -and
            $node.Variable
        }, $true) |
        ForEach-Object { $_.Variable.VariablePath.UserPath }
    foreach ($loopVar in $loops) {
        if ($paramNames -contains $loopVar) {
            Write-Host "  FAIL  $($itScript.Name): loop variable `$$loopVar collides with parameter of the same name"
            $bad = $true
        }
    }
}
Check "no loop variable shadows a parameter" (-not $bad)

# Two cells, not one.
#
# Archiving is deliberate about being late: the logs on disk after a run belong to
# *that* run, and the next invocation files them under the name the previous run
# recorded. So a single-cell test can never observe an archive -- it would have to
# assert something that has not happened yet, and asserting that is how the check
# ends up encoding a belief instead of a fact. The second cell is what makes the
# first cell's archive real, and it costs ten seconds.
$first = "smoke10s-a"
$second = "smoke10s-b"

# The whisper route's translation model lives in a llama-server on 8080 that
# replay.ps1 deliberately leaves alone so it stays warm between runs. Nothing
# started it, so the smoke test passed or failed according to whether some
# previous sweep happened to leave one running -- which is what happened here: it
# failed with zero events received and a message about translations, and both of
# those were really about a server that was never there.
#
# So the dependency is made explicit. If it cannot be started, say that, rather
# than reporting a downstream symptom.
if (-not $AudioNative) {
    if (-not (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)) {
        $Llama = (& .\scripts\resolve_llama.ps1 | Select-Object -Last 1).path
        $gguf = (Get-ChildItem "models\hy-mt2-1.8b\*.gguf" | Select-Object -First 1).FullName
        if (-not $gguf) {
            Write-Host "  FAIL  no Hy-MT2 weights in models\hy-mt2-1.8b"
            $bad = $true
        } else {
            Write-Host "  starting llama-server for Hy-MT2"
            Start-Process -FilePath $Llama -ArgumentList @(
                "-m", $gguf, "--host", "127.0.0.1", "--port", "8080",
                "-ngl", "99", "-c", "2048", "-t", "4", "-np", "1", "-lv", "4"
            ) -NoNewWindow -RedirectStandardOutput "logs\llama.out" -RedirectStandardError "logs\llama.err"
            $up = $false
            for ($i = 0; $i -lt 60; $i++) {
                Start-Sleep -Seconds 5
                if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) { $up = $true; break }
            }
            Check "translation backend on 8080" $up "the whisper route needs it"
            if (-not $up) { $bad = $true }
        }
    } else {
        Check "translation backend on 8080" $true "already up"
    }
}

Write-Host "smoke: 10s clip x2, $(if ($AudioNative) { 'audio-native' } else { 'whisper' })"
$started = Get-Date

foreach ($it in @($first, $second)) {
    $name = $it
    Write-Host ""
    Write-Host "  --- $name ---"
    try {
        & .\scripts\replay.ps1 $name -AudioNative:$AudioNative *>&1 |
            Where-Object { $_ -notmatch "^audio in:|SoundcardRuntimeWarning|warnings\.warn|^\s+\d+\.\d+s (asr|translation|status):" } |
            ForEach-Object { Write-Host "    $_" }
    }
    catch {
        Write-Host "  FAIL  $name threw"
        Write-Host $_.ScriptStackTrace
        Write-Host ($_.Exception | Out-String)
        $bad = $true
    }

    # During a cell the run's own log is still the un-archived name, because
    # archiving is deliberately late: the next invocation files it. Asserting on
    # the archived name here would be asserting on something that has not happened
    # yet, which is how a check ends up encoding a belief instead of a fact.
    $live = "logs\blog_3min_test.json"
    if (Test-Path $live) {
        $tr = (Select-String -Path $live -Pattern '"type":\s*"translation"' -AllMatches).Matches.Count
        Check "$name produced translations" ($tr -gt 0) "count=$tr"
    } else {
        Check "$name produced a replay log" $false
    }

    if (Test-Path "logs\run.$name.meta") {
        $meta = Get-Content "logs\run.$name.meta" -Raw -Encoding utf8 | ConvertFrom-Json
        Check "$name meta records the code" ([bool]$meta.code) "code=$($meta.code)"
        Check "$name meta records whether a draft was loaded" ($null -ne $meta.mtp_draft)
    } else {
        Check "$name wrote a meta file" $false
    }

    if (Test-Path "logs\run.pending.json") {
        $p = (Get-Content "logs\run.pending.json" -Raw | ConvertFrom-Json).name
        Check "pending marker names this run" ($p -eq $name) "says $p"
    } else {
        Check "pending marker names this run" $false "no marker"
    }

    # The summarise step is where the six-minute run died, after playing the whole
    # clip, so a smoke test that stops at "translations were produced" would not
    # have caught it. Two things assert it: the vad instrumentation the summary
    # reads is present in the log, and the summary itself runs on the archived log
    # rather than on a live one.
    # The live log, not the archived one: archiving is late, so during the cell
    # this run's perf events are still in the un-archived file. Looking for the
    # archived name here reported zero for a run that had recorded eight.
    if (Test-Path "logs\perf.log") {
        $vadEnd = (Select-String -Path "logs\perf.log" -Pattern " vad\.end " -AllMatches).Matches.Count
        Check "$name recorded why each segment ended" ($vadEnd -gt 0) "vad.end=$vadEnd"
    } else {
        Check "$name left a perf log with the vad instrumentation" $false
    }
}

Write-Host ""
Write-Host "  --- archive of the first cell, done by the second ---"
# This is the check the MTP loss would have failed: every cell produced output and
# every cell was filed under the wrong identity.
Check "first cell's perf log archived under its name" (Test-Path "logs\perf.$first.log")
Check "first cell's caption log archived under its name" (Test-Path "logs\perf_caption.$first.log")
Check "first cell's replay log archived under its name" (Test-Path "logs\blog_3min_test.$first.json")

# And the two reports that read those archives. The first cell's log is the one
# the six-minute run's failure happened in, so the summary has to run against an
# archived log, not only against the live one replay.ps1 measures.
Write-Host ""
Write-Host "  --- reports read the archived log ---"
& $Python scripts\audit_pauses.py "x=$first"
if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL  audit_pauses exited $LASTEXITCODE"; $bad = $true }
& $Python scripts\perf_compare.py $first "logs\perf.$first.log" "logs\perf_caption.$first.log" "logs\blog_3min_test.$first.json" --code smoke *>&1 |
    Where-Object { $_ -notmatch "^\s+\d+\.\d+s|^\s*\d+\.\d+s " } |
    ForEach-Object { Write-Host "    $_" }
if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL  perf_compare exited $LASTEXITCODE"; $bad = $true }

# After the last cell the leftovers are the last cell's own logs, still awaiting
# the next invocation. Asserting they are gone would be asserting the protocol is
# broken; what matters is that they are claimed, by the run that made them.
if (Test-Path "logs\run.pending.json") {
    $p = (Get-Content "logs\run.pending.json" -Raw | ConvertFrom-Json).name
    Check "leftover logs are claimed by the last run" ($p -eq $second) "says $p"
} else {
    Check "leftover logs are claimed by the last run" $false "logs on disk with no marker"
}

# The manifest verdict is reported, not asserted: a smoke clip can legitimately
# fail a completeness check, and a smoke test that hard-fails on that trains
# people to ignore it.
& $Python scripts\manifest.py check $first $second

$elapsed = (Get-Date) - $started
Write-Host ""
if ($bad) { Write-Host "smoke FAILED in $([math]::Round($elapsed.TotalSeconds, 0))s"; exit 1 }
Write-Host "smoke passed in $([math]::Round($elapsed.TotalSeconds, 0))s"