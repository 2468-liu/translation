param(
    [Parameter(Mandatory = $true)][string]$Name,
    [int]$StartupWait = 12,
    # gemma-audio branch: one model hears and translates, so whisper is not loaded.
    [switch]$AudioNative
)

# One replay of samples/blog_3min.mp4 against a freshly restarted server, then
# the metrics for it. Every run has to start the same way: the server owns its
# log files open, so they can only be rotated once it has been stopped, and the
# first ASR call of a run is cold.
#
#   .\scripts\replay.ps1 base1
#   .\scripts\replay.ps1 base2
#
# llama-server on 8080 is left alone so it stays warm across runs.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Python = "D:\minanconda\envs\pytorch\python.exe"

if (-not (Test-Path $Python)) { throw "python not found: $Python" }

Write-Host "stopping server and caption window (llama-server on 8080 stays up)"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if ($AudioNative) {
    # The audio model and whisper's model cannot both stay resident on a 4GB
    # card, and the two pipelines serve different models on 8080.
    Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 2

# The backend is resolved rather than hardcoded, and the version goes into this
# run's meta. Resolved for every run, not just the audio one: a backend that
# changes between arms makes two runs incomparable for the same reason a
# config.py edit used to, and the whisper arms are served by whatever is already
# on 8080, which is exactly the thing that goes unrecorded.
$backend = & .\scripts\resolve_llama.ps1 | Select-Object -Last 1
$Llama = $backend.path
$LlamaVersion = $backend.version

if ($AudioNative) {
    # Start llama-server here rather than via start.ps1, which also launches the
    # Python service. Same arguments, so both entry points stay in step.
    $gguf = (Get-ChildItem "$Root\models\gemma4-e2b\translate*.gguf" | Select-Object -First 1).FullName
    $mmproj = (Get-ChildItem "$Root\models\gemma4-e2b\*mmproj*.gguf" | Select-Object -First 1).FullName
    Write-Host "starting llama-server (audio model, projector on CPU)"
    Start-Process -FilePath $Llama -ArgumentList @(
        "-m", $gguf, "--mmproj", $mmproj, "--mmproj-device", "none",
        "--host", "127.0.0.1", "--port", "8080",
        "-ngl", "99", "-c", "2048", "-t", "4", "--jinja", "-lv", "4"
    ) -NoNewWindow `
        -RedirectStandardOutput "logs\llama.out" -RedirectStandardError "logs\llama.err"
    # Loading the weights takes far longer than the Python service does, so the
    # socket can stay closed well past the usual wait. Poll instead of sleeping:
    # the port answering means the model is actually resident.
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
            $ready = $true
            break
        }
        Write-Host "  waiting for llama-server ($((($i + 1) * 5))s)"
    }
    if (-not $ready) { throw "llama-server did not come up on 8080" }
    Write-Host "  llama-server ready"
}

# The logs still on disk belong to the PREVIOUS run, so that is the name they
# have to be archived under. Rotating them into this run's name instead files
# every result one run late, which is exactly the kind of quiet mislabelling
# these logs exist to rule out.
$Pending = "logs\run.pending.json"
$previous = $null
if (Test-Path $Pending) {
    $previous = (Get-Content $Pending -Raw | ConvertFrom-Json).name
}

if ($previous) {
    $rotate = @(
        @("logs\perf.log", "logs\perf.$previous.log"),
        @("logs\perf_caption.log", "logs\perf_caption.$previous.log"),
        @("logs\blog_3min_test.json", "logs\blog_3min_test.$previous.json")
    )
    foreach ($pair in $rotate) {
        if (Test-Path $pair[0]) {
            Move-Item -Force $pair[0] $pair[1]
            Write-Host "archived previous run: $($pair[0]) -> $($pair[1])"
        }
    }
} elseif (Test-Path "logs\perf.log") {
    throw "logs\perf.log exists but logs\run.pending.json does not; cannot tell which run it belongs to"
}
Remove-Item $Pending -Force -ErrorAction SilentlyContinue

$env:PYTHONUNBUFFERED = "1"
$env:TRANSLATION_PERF = "1"
Remove-Item Env:TRANSLATION_PERF_LOG -ErrorAction SilentlyContinue
if ($AudioNative) {
    $env:TRANSLATION_AUDIO_NATIVE = "1"
} else {
    Remove-Item Env:TRANSLATION_AUDIO_NATIVE -ErrorAction SilentlyContinue
}

# Stamp the run now, while the tree still looks the way it did when the audio
# played. perf_compare reading git later would only report where HEAD is when
# the numbers are analysed, which is not the same thing.
# PowerShell hands back an array of lines, so join before trimming.
$sha = ((& git rev-parse --short HEAD) -join "").Trim()
$dirty = if ((& git status --porcelain) -join "") { "+dirty" } else { "" }
$stamp = "$sha$dirty"
Write-Host "code $stamp"

Write-Host "starting server"
Start-Process -FilePath $Python -ArgumentList "-m", "server" -NoNewWindow | Out-Null
Start-Sleep -Seconds $StartupWait

$listening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if (-not $listening) { throw "server did not come up on 8765" }
Write-Host "server pid $($listening.OwningProcess)"

Write-Host "replaying samples/blog_3min.mp4 (~3.5 min)"
& $Python scripts\blog_video_test.py play
$replayExit = $LASTEXITCODE

# A replay that died partway still leaves a log behind, and measuring that would
# report a short window with no indication anything was wrong.
if ($replayExit -ne 0 -or -not (Test-Path "logs\blog_3min_test.json")) {
    throw "replay did not finish (exit $replayExit); logs left in place, not measuring them"
}

# A replay that produced no translations at all is a void run, not a run.
#
# The whisper route serves its translation model from llama-server on 8080, which
# replay.ps1 deliberately leaves alone so it stays warm between runs. If that
# server is not up the ASR half still works, the material still plays, the client
# still exits 0 -- and the run looks fine while containing nothing but ASR. Four
# runs were lost to this before the check existed.
$trCount = (Select-String -Path "logs\blog_3min_test.json" -Pattern '"type":\s*"translation"' -AllMatches).Matches.Count
if ($trCount -eq 0) {
    throw "no translations in this run; the translation model was not serving."
}

# Record the run only once it has completed, and mark its logs as pending so the
# NEXT invocation knows which name to archive them under.
#
# The thresholds go in too. The stamp alone was not enough: four runs in one
# sweep all read "+dirty" because the sweep was rewriting config.py between
# them, so no two of them could be shown to be the same code, and nothing in the
# meta said which partial interval any of them had run at.
$effective = & $Python -c @"
import json
from server import config
print(json.dumps({
    'partial_interval_ms': config.PARTIAL_INTERVAL_MS,
    'gemma_min_silence_ms': config.GEMMA_MIN_SILENCE_MS,
    'gemma_max_utterance_ms': config.GEMMA_MAX_UTTERANCE_MS,
    'audio_context_sentences': config.AUDIO_CONTEXT_SENTENCES,
    'audio_repeat_penalty': config.AUDIO_REPEAT_PENALTY,
    'audio_chinese_share_min': config.AUDIO_CHINESE_SHARE_MIN,
}))
"@
@{
    name = $Name
    code = $stamp
    when = (Get-Date).ToString("o")
    audio_native = [bool]$AudioNative
    llama_version = $LlamaVersion
    thresholds = ($effective -join "").Trim()
} | ConvertTo-Json | Set-Content -Encoding utf8 "logs\run.$Name.meta"
@{ name = $Name } | ConvertTo-Json | Set-Content -Encoding utf8 $Pending

Write-Host ""
& $Python scripts\perf_compare.py $Name logs\perf.log logs\perf_caption.log logs\blog_3min_test.json --code $stamp