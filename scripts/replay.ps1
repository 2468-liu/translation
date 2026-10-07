param(
    [Parameter(Mandatory = $true)][string]$Name,
    [int]$StartupWait = 12,
    # gemma-audio branch: one model hears and translates, so whisper is not loaded.
    [switch]$AudioNative,
    # Leave a llama-server that is already up alone.
    #
    # This exists because of a silent twelve-run loss. A sweep starts its own
    # backend -- that is how MTP gets loaded, since the draft flags are command
    # line arguments and not config values -- and then replay.ps1 killed it and
    # started a second one without them. So every "MTP on" cell ran with MTP off,
    # under a name that said otherwise, and the sweep reported a clean 2x2.
    #
    # The tell was two "llama-server ready" lines per cell. The arm name was the
    # only record of what was configured, so nothing caught it.
    [switch]$KeepBackend
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
if ($AudioNative -and -not $KeepBackend) {
    # The audio model and whisper's model cannot both stay resident on a 4GB
    # card, and the two pipelines serve different models on 8080.
    Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 2

# Report who is still holding the card, rather than assuming the kills above got
# everything.
#
# The kill above only matches python.exe and llama-server.exe by name, so a
# server started some other way survives it. One did: a background diagnostic
# left pid 9128 alive on the GPU for hours, and it was still holding 731 MiB when
# the power baseline was about to be measured. A baseline taken with a stray
# process on the card is not a baseline, and nothing in the run's own output said
# so -- the manifest was clean, the numbers looked plausible, and the delta would
# have been quietly wrong.
#
# So the survivors are named on screen. This does not fail the run: another arm
# may legitimately own the card, and a judge that refuses to run because of that
# is worse than one that says who is there.
$gpuHolders = & nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>$null
if ($gpuHolders) {
    Write-Host "GPU still held by:"
    $gpuHolders | ForEach-Object { Write-Host "    $_" }
} else {
    Write-Host "GPU: no compute processes"
}

# The backend is resolved rather than hardcoded, and the version goes into this
# run's meta. Resolved for every run, not just the audio one: a backend that
# changes between arms makes two runs incomparable for the same reason a
# config.py edit used to, and the whisper arms are served by whatever is already
# on 8080, which is exactly the thing that goes unrecorded.
$backend = & .\scripts\resolve_llama.ps1 | Select-Object -Last 1
$Llama = $backend.path
$LlamaVersion = $backend.version

# The whisper route takes its translation model from whatever is on 8080, and this
# script deliberately leaves that server alone so it stays warm between runs. It
# therefore assumes the caller started one -- and without a check, a missing server
# shows up three steps later as "no translations in this run", which reads as a
# pipeline fault and is not one.
#
# Probed over HTTP rather than by process name, because a process name only proves
# something is running. The port could be held by a different instance, a different
# model, or something that is not a server at all.
#
# It throws rather than starting one. smoke.ps1 does start a backend, and that is
# correct there because a smoke test is meant to be self-contained; a replay is a
# measurement of a configured system, and quietly launching a translation model
# mid-run would put a step nobody asked for between two arms. There is no
# --no-translate escape either: skipping translation makes this a different script,
# and a run that is half a replay should not be reportable as a replay.
if (-not $AudioNative) {
    $expectedModel = if (Test-Path "models\hy-mt2-1.8b") {
        (Get-ChildItem "models\hy-mt2-1.8b\*.gguf" | Select-Object -First 1).BaseName
    } else { "" }

    $served = $null
    try {
        $probe = Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/models" `
                                  -TimeoutSec 2 -ErrorAction Stop
        $served = ($probe.data | Select-Object -First 1).id
    } catch {
        throw @"
翻译后端不在 8080 上，回放不会有译文。
  replay.ps1 假定调用方已启动它；smoke.ps1 会自动启动，本脚本不会。
  请先起一个 hy-mt2 的 llama-server 再回放（例如 start.ps1 里的那条命令）。
  端口占用或模型不对也会走到这里：$($_.Exception.Message)
"@
    }

    if ($expectedModel -and $served -and ($served -notlike "*hy-mt2*")) {
        throw @"
翻译后端在 8080 响应，但加载的不是 hy-mt2：$served（期望 $expectedModel）。
  replay.ps1 假定 8080 上是 hy-mt2；请检查 start.ps1 的加载顺序。
  能响应 != 是对的模型 —— 这是同一类错误的另一种形态。
"@
    }
    Write-Host "translation backend: $served on 8080 (expected hy-mt2)"
}

if ($AudioNative -and $KeepBackend) {
    # Somebody else owns the backend, and it was started with arguments this
    # script cannot see -- MTP's --model-draft is one. Starting a second one here
    # would silently discard those arguments, so this path only waits.
    #
    # That is the whole point of -KeepBackend, so read the live process rather
    # than assuming. What it was launched with goes into the meta below; an arm
    # name is not evidence of what ran.
    $live = Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $live) { throw "-KeepBackend was asked for but no llama-server is running" }
    Write-Host "keeping the llama-server that is already up (started by the caller)"
}
elseif ($AudioNative) {
    # Start llama-server here rather than via start.ps1, which also launches the
    # Python service. Same arguments, so both entry points stay in step.
    $gguf = (Get-ChildItem "$Root\models\gemma4-e2b\translate*.gguf" | Select-Object -First 1).FullName
    $mmproj = (Get-ChildItem "$Root\models\gemma4-e2b\*mmproj*.gguf" | Select-Object -First 1).FullName
    # Where the projector runs, and it is a real choice rather than a fallback.
    #
    # "none" means don't offload it at all -- the projector stays on the CPU --
    # and it was set because the card was believed to be at 3774 of 4096 MiB,
    # leaving 320. That figure was wrong by half; nvidia-smi reads 1842 and the
    # projector is 532 MiB, so on the GPU it would sit at about 2374 of 4096.
    #
    # The default is still CPU so the sweep in flight stays comparable. Every
    # Gemma number measured before this existed carries "projector on CPU" as a
    # load condition and has to be read that way.
    $mmprojDevice = if ($env:TRANSLATION_MMPROJ_DEVICE) { $env:TRANSLATION_MMPROJ_DEVICE } else { "none" }
    $mmprojWhere = if ($mmprojDevice -eq "none") { "projector on CPU" } else { "projector on $mmprojDevice" }
    Write-Host "starting llama-server (audio model, $mmprojWhere)"
    Start-Process -FilePath $Llama -ArgumentList @(
        "-m", $gguf, "--mmproj", $mmproj, "--mmproj-device", $mmprojDevice,
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
    # Leftover logs with nothing claiming them. Refusing outright is what stalled
    # the last sweep: one cell died, the pending marker was never written, and
    # every cell after it refused to start because it could not tell whose logs
    # these were. Quarantine them instead of stopping -- the refusal was right
    # about the ambiguity and wrong about the remedy.
    #
    # Quarantined rather than deleted, because those logs are the only record of
    # whatever killed the previous cell.
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $orphan = "logs\orphan\$stamp"
    New-Item -ItemType Directory -Force -Path $orphan | Out-Null
    # Not $name. PowerShell variables are case-insensitive, so $name here is
    # $Name -- the run's own name -- and the loop silently replaced it with the
    # last filename. Every archive after that went to "perf.blog_3min_test.json.log"
    # and the meta recorded the wrong arm. Reading the file does not reveal this;
    # running it does.
    foreach ($orphanFile in @("perf.log", "perf_caption.log", "blog_3min_test.json")) {
        $path = "logs\$orphanFile"
        if (Test-Path $path) {
            Move-Item -Force $path (Join-Path $orphan $orphanFile)
            Write-Host "quarantined unclaimed log: $path -> $orphan"
        }
    }
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

# The clip and its duration come from the file, not from a constant. Every number
# this file used to carry about the sample -- the name, the "about 3.5 minutes" --
# described one clip, and pointing the replay at a different one made the message
# a lie while the run itself was fine.
$sampleWav = if ($env:TRANSLATION_SAMPLE_WAV) { $env:TRANSLATION_SAMPLE_WAV } else { "samples\blog_3min.wav" }
$sampleSec = & $Python -c "import soundfile as sf,sys; print(f'{sf.info(sys.argv[1]).duration:.1f}')" $sampleWav
Write-Host "replaying $sampleWav ($([math]::Round($sampleSec / 60, 2)) min)"
# stderr merged into stdout, and ErrorAction dropped for the call.
#
# With $ErrorActionPreference = "Stop" in effect, the first line a native command
# writes to stderr becomes a terminating error, which aborts the pipeline before
# the rest of the traceback is read. So the process printed "Traceback (most
# recent call last):" and nothing after it -- twice now, on two different bugs,
# both of which were one line of output away from being diagnosed immediately.
# The relaxed preference is scoped to this one call; the checks below still throw.
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Python scripts\blog_video_test.py play 2>&1 | ForEach-Object { "$_" }
$replayExit = $LASTEXITCODE
$ErrorActionPreference = $previousPreference

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
# What the serving backend was actually launched with, read off the live process.
#
# MTP lives only in the command line -- there is no config value for it and the
# meta used to record nothing about it, so an arm's name was the only claim that
# a draft was loaded. That is how twelve runs came back labelled "MTP on" while
# every one of them ran with MTP off: the sweep started a backend with the draft
# flags and this script killed it and started another without them.
#
# Read it here, at the end, from the process that actually served the run. If the
# caller wanted a draft and none is loaded, that is a throw rather than a note.
$liveCmd = (Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue |
    Select-Object -First 1).CommandLine
$draftLoaded = [bool]($liveCmd -and $liveCmd -match "model-draft")
$mmprojLive = if ($liveCmd -match "--mmproj-device\s+(\S+)") { $Matches[1] } else { "unset" }
Write-Host "### backend as launched: draft=$draftLoaded mmproj-device=$mmprojLive"
if ($env:TRANSLATION_EXPECT_DRAFT -eq "1" -and -not $draftLoaded) {
    throw "$Name wanted a draft model loaded and the serving llama-server has none"
}
if ($env:TRANSLATION_EXPECT_DRAFT -eq "0" -and $draftLoaded) {
    throw "$Name wanted no draft model and the serving llama-server has one"
}
# Same argument for the projector, and it is the one this file used to get wrong
# twice: the number came from a card reading that was out by half, and then the
# mechanism offered for the resulting decision was never measured. Assert against
# the process, so a cell cannot run under a placement its name does not have.
if ($env:TRANSLATION_EXPECT_MMPROJ -and $mmprojLive -ne $env:TRANSLATION_EXPECT_MMPROJ) {
    throw "$Name wanted mmproj-device $($env:TRANSLATION_EXPECT_MMPROJ) and the serving llama-server has $mmprojLive"
}

@{
    name = $Name
    code = $stamp
    when = (Get-Date).ToString("o")
    audio_native = [bool]$AudioNative
    llama_version = $LlamaVersion
    mmproj_device = if ($env:TRANSLATION_MMPROJ_DEVICE) { $env:TRANSLATION_MMPROJ_DEVICE } else { "none" }
    # From the process, not from the arm name or an environment variable.
    mtp_draft = $draftLoaded
    mmproj_device_live = $mmprojLive
    thresholds = ($effective -join "").Trim()
} | ConvertTo-Json | Set-Content -Encoding utf8 "logs\run.$Name.meta"
@{ name = $Name } | ConvertTo-Json | Set-Content -Encoding utf8 $Pending

Write-Host ""
& $Python scripts\perf_compare.py $Name logs\perf.log logs\perf_caption.log logs\blog_3min_test.json --code $stamp