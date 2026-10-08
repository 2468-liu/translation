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

# Port and process probes that do not need WMI. WMI is refused in a confined
# session, and every silenced WMI call here used to read as a passing check.
. (Join-Path $PSScriptRoot "probes.ps1")

if (-not (Test-Path $Python)) { throw "python not found: $Python" }

Write-Host "stopping server and caption window (llama-server on 8080 stays up)"
# Get-Process, not Get-CimInstance.
#
# WMI/CIM is refused outright in a confined session ("Get-CimInstance : 拒绝访问",
# HRESULT 0x80041003), and with $ErrorActionPreference = "Stop" that refusal is a
# terminating error -- so the script died on its first line of work and no replay
# ever ran. Process listing through the provider path needs no WMI service.
Get-Process -Name python, pythonw -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
if ($AudioNative -and -not $KeepBackend) {
    # The audio model and whisper's model cannot both stay resident on a 4GB
    # card, and the two pipelines serve different models on 8080.
    Get-Process -Name llama-server -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
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
#
# Recording the version was not enough, and that is what this run pins for. Before,
# the resolver defaulted to a hardcoded 2.47.0 while start.ps1 had its own copy
# defaulting to newest installed -- so the measurements were comparable by accident,
# because the measuring path and the running path answered the question two different
# ways. Nothing enforced it. Today LM Studio installing a build would have moved every
# later run off the version the earlier numbers came from, with the meta block
# honestly recording the new version and the two runs still being incomparable.
#
# So this pins, and takes the pinned number from server/config.py rather than
# hardcoding it a second time here. -LlamaVersion still wins for a one-off A/B on a
# different build; that arm is then comparable to itself and not to REPORT.md, which
# is what the meta block's version field is for.
$LlamaVersionWanted = $env:TRANSLATION_MEASUREMENT_LLAMA_VERSION
if (-not $LlamaVersionWanted) {
    $LlamaVersionWanted = (& $Python -c "from server.config import MEASUREMENT_LLAMA_VERSION; print(MEASUREMENT_LLAMA_VERSION)" 2>&1 | Out-String).Trim()
}
$resolveArgs = @{ Version = $LlamaVersionWanted }
$backend = & .\scripts\resolve_llama.ps1 @resolveArgs | Select-Object -Last 1
$Llama = $backend.path
$LlamaVersion = $backend.version
Write-Host "measurement llama.cpp: $LlamaVersion (asked for $LlamaVersionWanted, via $($backend.selected))"

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
    # Named by pattern, and the pattern is checked for ambiguity rather than
    # resolved by taking the first hit. This used to be
    # `*Q4_K_M*.gguf | Select-Object -First 1`, and the directory holds two matches
    # now, so the expectation could name a model that was never loaded. It only ever
    # appears in the mismatch message, but a guard that reports the wrong expectation
    # is worse than one that reports none -- and this guard is the only thing that
    # notices when the served weights are not the ones every number was measured on.
    # Get-HyMt2Gguf throws on more than one match rather than picking.
    $expectedModel = if (Test-Path "models\hy-mt2-1.8b") {
        Get-HyMt2Gguf -BaseName
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

    # The build actually serving, not the one this script resolved. Resolving proves
    # nothing here: replay deliberately leaves the 8080 server alone so it stays warm
    # across runs, so every whisper arm is served by whatever the caller started, and
    # $LlamaVersion is only what would be started if the audio-native path needed one.
    # Recording the resolved version in the meta while serving another one is how
    # "llama_version 2.47.0" ends up in a run that actually used something else --
    # the meta block is read later by someone deciding whether two runs are comparable,
    # and it has to be right for them.
    #
    # Read from the process's own command line, and the model's directory carries the
    # build name, so this is an observation. When the command line cannot be read
    # (WMI refused) that is reported rather than treated as agreement: "could not find
    # out" and "it is the right one" are different facts.
    $liveLlama = $null
    $liveCmd = Get-ProcessCommandLine -Name llama-server
    if ($liveCmd) {
        # The platform segment contains an underscore -- win-x86_64-nvidia-cuda12-avx2 --
        # so the class has to include it. It read as [A-Za-z0-9\-] first, and that failed
        # to match the real process, which made the guard refuse every run on this
        # machine regardless of the build. A guard that always says no is not a guard;
        # it is a wall, and it fails the same way as a missing one: no measurement.
        $hit = [regex]::Match($liveCmd, 'llama\.cpp-[A-Za-z0-9_\-]+?-(\d+\.\d+\.\d+)')
        if ($hit.Success) { $liveLlama = $hit.Groups[1].Value }
    }
    if (-not $liveLlama) {
        throw @"
读不出 8080 上那个 llama-server 是哪一版，回放拒绝继续。
  期望 $LlamaVersion；进程命令行读不出来（WMI 被拒），所以无法确认它是不是那一版。
  「读不出」不等于「是那一版」——这正是本条守卫要拦的那件事：REPORT 里每个延迟数都是
  $LlamaVersion 上的，而如果这一版不同，两次运行就不可比，而日志里看不出。
  读得出命令行时，它应当形如 ...\llama.cpp-win-x86_64-nvidia-cuda12-avx2-<版本>\llama-server.exe
"@
    }
    if ($liveLlama -ne $LlamaVersion) {
        throw @"
8080 上的 llama-server 是 $liveLlama，但测量钉的是 $LlamaVersion。
  两个运行会不可比，而每一条记录都会各自诚实地写下自己那版，于是看不出来。
  换回测量版本再跑：
      scripts\start.ps1 -LlamaVersion $LlamaVersion
  或者显式声明你要跑另一版（这一次运行就只与自己可比，REPORT 的数与它无关）：
      `$env:TRANSLATION_MEASUREMENT_LLAMA_VERSION = "$liveLlama"
"@
    }
    Write-Host "translation backend: $served on 8080, served by llama.cpp $liveLlama (measurement build)"
}

if ($AudioNative -and $KeepBackend) {
    # Somebody else owns the backend, and it was started with arguments this
    # script cannot see -- MTP's --model-draft is one. Starting a second one here
    # would silently discard those arguments, so this path only waits.
    #
    # That is the whole point of -KeepBackend, so read the live process rather
    # than assuming. What it was launched with goes into the meta below; an arm
    # name is not evidence of what ran.
    $live = Get-ProcessCommandLine -Name llama-server
    if (-not (Get-Process -Name llama-server -ErrorAction SilentlyContinue)) {
        throw "-KeepBackend was asked for but no llama-server is running"
    }
    if (-not $live) { throw "-KeepBackend was asked for but the running llama-server's command line could not be read (WMI refused), so what it was launched with cannot be recorded" }
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
        if (Test-TcpPort -Port 8080) {
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

# The replay's audio source is part of what a replay *is*, so the script decides
# it rather than the shell that invoked it.
#
# A replay injects PCM over the WebSocket. If the server is left on its product
# default (loopback, which captures the machine's speakers through WASAPI) then
# ws_server ignores every binary frame -- by design, since exactly one producer
# may own the splitter -- and the replay produces a clean run with no utterances
# in it: vad.end=0, no translations, no error. That happened here, and the run
# looked like a translation-model failure three steps downstream.
#
# So: default to socket, which is the measurement口径 recorded in REPORT 24-27,
# and let a caller who deliberately wants to measure the loopback path ask for it
# explicitly. Print it, because "which source was this run" belongs in the run
# log next to the code stamp rather than in whoever's shell history.
$env:TRANSLATION_AUDIO_SOURCE = if ($env:TRANSLATION_AUDIO_SOURCE) {
    $env:TRANSLATION_AUDIO_SOURCE
} else {
    "socket"
}
Write-Host "audio source: $($env:TRANSLATION_AUDIO_SOURCE) (socket = PCM injected by this replay)"

# Stamp the run now, while the tree still looks the way it did when the audio
# played. perf_compare reading git later would only report where HEAD is when
# the numbers are analysed, which is not the same thing.
# PowerShell hands back an array of lines, so join before trimming.
$sha = ((& git rev-parse --short HEAD) -join "").Trim()
$dirty = if ((& git status --porcelain) -join "") { "+dirty" } else { "" }
$stamp = "$sha$dirty"
Write-Host "code $stamp"

Write-Host "starting server"
# -PassThru, not a lookup afterwards. The lookup was `Get-Process python | Sort-Object
# StartTime -Descending | Select-Object -First 1`, which is wrong twice over: reading
# StartTime needs a privilege a confined session does not grant, so it threw
# "Access is denied" and aborted the run before the clip played; and even where it
# works, "newest python" is a guess about which process is the server rather than
# knowledge of it. The process object handed back here is the one that was started.
$serverProc = Start-Process -FilePath $Python -ArgumentList "-m", "server" -NoNewWindow -PassThru
Start-Sleep -Seconds $StartupWait

# Poll the socket rather than asking the OS who owns the port: Get-NetTCPConnection
# is CIM-based and is refused outright in a confined session, where it returns
# nothing and reads as "the server never came up" while it is in fact serving.
# Loading the recogniser takes longer than the startup wait, so a single check is
# not enough even when the query works.
$up = $false
for ($i = 0; $i -lt 36; $i++) {
    if (Test-TcpPort -Port 8765) { $up = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $up) { throw "server did not come up on 8765" }
Write-Host "server pid $($serverProc.Id)"

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
# thresholds go in too. The stamp alone was not enough: four runs in one
# sweep all read "+dirty" because the sweep was rewriting config.py between
# them, so no two of them could be shown to be the same code, and nothing in the
# meta said which partial interval any of them had run at.
#
# asr_model is in that dict for the same reason, and it is the one field whose
# absence is hardest to notice. thresholds covers the tunables, but the ASR model
# is a path, not a threshold: nothing in the run summary mentions it, so an arm
# named "turbo-er" was the only claim that turbo had run. That is the same shape
# as the twelve "MTP on" arms that all ran with MTP off -- a name standing in for
# a fact. Read asr_model, not the arm name.
# One line on purpose. This was a multi-line here-string piped into `python -c`,
# and PowerShell mangled it into an unterminated dict -- the run then recorded
# `thresholds: ""` while reporting nothing wrong, so the meta said the
# configuration was unrecorded and the reader had no way to know why. A dict
# built by assignment has no braces for the outer language to lose.
$effective = & $Python -c "import json; from server import config; d = {}; d['partial_interval_ms'] = config.PARTIAL_INTERVAL_MS; d['gemma_min_silence_ms'] = config.GEMMA_MIN_SILENCE_MS; d['gemma_max_utterance_ms'] = config.GEMMA_MAX_UTTERANCE_MS; d['audio_context_sentences'] = config.AUDIO_CONTEXT_SENTENCES; d['audio_repeat_penalty'] = config.AUDIO_REPEAT_PENALTY; d['audio_chinese_share_min'] = config.AUDIO_CHINESE_SHARE_MIN; d['vad_min_speech_ms'] = config.VAD_MIN_SPEECH_MS; d['vad_merge_window_ms'] = config.VAD_MERGE_WINDOW_MS; d['mt_backend'] = config.MT_BACKEND; d['mt_context_sentences'] = config.MT_CONTEXT_SENTENCES; d['asr_model'] = config.ASR_MODEL_DIR.name; d['asr_fallback'] = config.ASR_FALLBACK_DIR.name; d['asr_backend'] = config.ASR_BACKEND; print(json.dumps(d))"
if (-not $effective) {
    throw "could not read the effective configuration; refusing to record a run whose thresholds would be blank"
}
$effective = ($effective -join "").Trim()
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
#
# "Could not be read" is its own answer, and it is a throw rather than a False.
# The WMI query this used to make is refused in a confined session and the refusal
# was silenced, so $liveCmd was $null, $draftLoaded was $false, and the assertion
# below read that as "no draft" -- a check that cannot run reporting a pass.
$liveCmd = Get-ProcessCommandLine -Name llama-server
if (-not $liveCmd -and ($env:TRANSLATION_EXPECT_DRAFT -or $env:TRANSLATION_EXPECT_MMPROJ)) {
    throw ("the serving llama-server's command line could not be read (WMI refused), so " +
        "whether it loaded a draft or a projector cannot be verified; refusing to record " +
        "this run as if the check had passed")
}
# mtp_draft answers one narrow question: is a separate draft MODEL FILE loaded?
# That is the Gemma-style path, --model-draft <file>.
#
# It is not the same question as "is speculative decoding on". llama.cpp also
# enables MTP through --spec-type draft-mtp, which names no file at all -- so
# hy-mt2 running with MTP records draft=False here while MTP is actually on, and
# the meta reads like a settled fact. Same failure as the arm name one level
# down: the field name says what it measures, and what it measures is narrower
# than what a reader will assume.
#
# spec_args below covers both spellings. Read that one for "was speculative
# decoding enabled at all"; read mtp_draft only when the separate-file path is
# specifically what is meant.
$draftLoaded = [bool]($liveCmd -and $liveCmd -match "model-draft")

# Every speculative-decoding flag the serving backend was launched with, value
# included where one follows. Both ways of turning it on:
#   --model-draft <file>     Gemma-style, a separate draft model
#   --spec-type draft-mtp    hy-mt2-style, no file, named by type
# Empty array when there are none, and null when the command line could not be
# read -- the same distinction process_state_readable already makes, and for the
# same reason: "we could not look" is not "it was off".
$specArgs = $null
if ($liveCmd) {
$specArgs = @()
$tokens = @($liveCmd -split '\s+' | Where-Object { $_ })
for ($i = 0; $i -lt $tokens.Count; $i++) {
if ($tokens[$i] -like "--spec-*" -or $tokens[$i] -like "--model-draft") {
$specArgs += $tokens[$i]
$nxt = if ($i + 1 -lt $tokens.Count) { $tokens[$i + 1] } else { "" }
if ($nxt -and -not $nxt.StartsWith("-")) { $specArgs += $nxt }
}
}
}
$mmprojLive = if ($liveCmd -and $liveCmd -match "--mmproj-device\s+(\S+)") { $Matches[1] } else { "unset" }
Write-Host "### backend as launched: draft-file=$draftLoaded spec-args=$($specArgs -join ' ') mmproj-device=$mmprojLive$(if (-not $liveCmd) { ' (command line unreadable)' })"
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

# One nvidia-smi call, four fields, parsed into a hashtable for the meta. Chosen
# because it needs no install and no privileges, and because the CPU side has no
# equivalent here: MSAcpi_ThermalZoneTemperature does not exist on this machine.
#
# "idle" is included rather than temperature alone, because temperature alone
# cannot distinguish "cool because idle" from "cool because throttling" -- and a
# throttling reading is the one that would invalidate comparing two arms.
$GpuProbe = $null
try {
    $gpuLine = (& nvidia-smi --query-gpu=temperature.gpu,power.draw,clocks_throttle_reasons.active,utilization.gpu --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    if ($gpuLine) {
        $g = @($gpuLine -split ',' | ForEach-Object { $_.Trim() })
        if ($g.Count -ge 4) {
            $GpuProbe = @{
                temperature_c = [double]$g[0]
                power_w       = [double]$g[1]
                throttle_raw  = $g[2]
                utilization_pct = [double]$g[3]
            }
        }
    }
} catch {
    $GpuProbe = $null
}

@{
    name = $Name
    code = $stamp
    when = (Get-Date).ToString("o")
    audio_native = [bool]$AudioNative
    llama_version = $LlamaVersion
    mmproj_device = if ($env:TRANSLATION_MMPROJ_DEVICE) { $env:TRANSLATION_MMPROJ_DEVICE } else { "none" }
    # From the process, not from the arm name or an environment variable.
    #
    # null when the command line could not be read (WMI refused in a confined
    # session). null is honest and false is not: "no draft was loaded" and "we
    # could not find out" are different facts, and the meta is read later by
    # someone deciding whether two runs are comparable.
    #
    # SCOPE: this is the Gemma-style separate draft FILE only (--model-draft). It
    # is NOT "was speculative decoding on". llama.cpp also enables MTP through
    # --spec-type draft-mtp, which names no file, so hy-mt2 with MTP on records
    # mtp_draft=false here. Use spec_args for that question.
    mtp_draft = if ($liveCmd) { $draftLoaded } else { $null }
    # Every --spec-* flag plus --model-draft, value included where one follows.
    # Read this to know whether speculative decoding was enabled at all, on either
    # backend; read mtp_draft only when the separate-file path is what is meant.
    # Empty array means "launched with none"; null means "could not read the
    # command line", which is a different claim.
    spec_args = $specArgs
    mmproj_device_live = if ($liveCmd) { $mmprojLive } else { $null }
    process_state_readable = [bool]$liveCmd
    thresholds = ($effective -join "").Trim()
    # GPU state, sampled after the run, so it is the resting state rather than the
    # load -- which is what makes it comparable between arms.
    #
    # Temperature is here because it is the one thermal number this machine gives
    # up. The ACPI route does not exist on it: MSAcpi_ThermalZoneTemperature returns
    # nothing, the same absence as the zero thermal zones found earlier, and Windows
    # has no reliable user-space CPU temperature path without a third-party tool.
    # GPU temperature costs one nvidia-smi call and no install.
    #
    # null when nvidia-smi is absent or refuses, for the reason mtp_draft above
    # uses: "we could not read it" and "it was zero" are different facts.
    gpu = if ($null -ne $GpuProbe) { $GpuProbe } else { $null }
    gpu_readable = [bool]($null -ne $GpuProbe)
} | ConvertTo-Json | Set-Content -Encoding utf8 "logs\run.$Name.meta"
@{ name = $Name } | ConvertTo-Json | Set-Content -Encoding utf8 $Pending

Write-Host ""
& $Python scripts\perf_compare.py $Name logs\perf.log logs\perf_caption.log logs\blog_3min_test.json --code $stamp