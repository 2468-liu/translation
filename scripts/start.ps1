<#
.SYNOPSIS
    启动本地后端：llama-server（8080）+ WebSocket 服务（8765）。

.DESCRIPTION
    扩展的"开始"按钮通过 Native Messaging host 启动的就是这个脚本，所以它必须
    能被静默调用（无参数、无窗口）。scripts\start.ps1 是它的唯一名字，改名前先看
    scripts\native_host.py 第 119 行——那里写死了这个路径。

    这里包含两个 Python 环境的选择和一个 llama.cpp 版本的选择，两者都写在日志里，
    因为"跑的是哪个"曾经只能靠猜。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -LlamaVersion 2.47.0
#>
[CmdletBinding()]
param(
    # 指定 llama.cpp 版本。不指定就用本机已装最新（shipping 的行为）。
    # 常用值在 server/config.py 的 MEASUREMENT_LLAMA_VERSION，那是 REPORT 里所有
    # 延迟数所在的版本。不再用 -UsePinned：那个开关只等于"指定成测量版本"，
    # 而它和 resolve_llama.ps1 的 -AutoLatest 是两个方向相反的默认值，
    # 详见 resolve_llama.ps1 的文件头。
    [string] $LlamaVersion = "",
    # 直接指定 llama-server.exe，优先级最高。
    [string] $LlamaPath = "",
    # 直接指定 Python 解释器，优先于 TRANSLATION_PYTHON 与 conda 环境。
    [string] $Python = "",
    # 只解析"会选哪一版、用哪个环境"并打印，不启动任何进程。排障用：
    # 想知道扩展点开始会拉起什么，又不想真把后端起起来。
    [switch] $DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Get-HyMt2Gguf 住在这里：它遇到多个匹配的权重会 throw，而一个歧义的后端
# 不能被静默选一个。start.ps1 原来写的是
# `Get-ChildItem *Q4_K_M*.gguf | Select-Object -First 1`，而目录里现在有两个匹配
# （原版和 10-08 新放进来的 StreamRevise-v4），所以起哪个权重是一次抛硬币。
. (Join-Path $PSScriptRoot "probes.ps1")

# 落到哪一版，事后要能从日志回答。Write-Host 在 native host 下进 DEVNULL，所以
# 结论同时写进 logs\start.log；这是排障时唯一还留着的记录。
$LogDir = Join-Path $Root "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$StartLog = Join-Path $LogDir "start.log"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-Note([string] $Text) {
    Write-Host $Text
    # AppendAllText 而不是 Add-Content：PowerShell 5.1 的 -Encoding utf8 会写 BOM，
    # 于是每条追加都会在文件中间插一个 BOM。启动日志本来就会被 grep。
    [System.IO.File]::AppendAllText($StartLog, "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Text`r`n", $Utf8NoBom)
}

# 括号自检。
#
# src 曾经把 MTP 分支的收尾写成 `} else {`，位置在内层 if 里面，于是整份文件
# 解析失败。PowerShell 是"先整份解析、再执行第一行"，所以脚本一行都没跑，
# 扩展那边看到的只是"本地后端启动超时（90 秒）"——错误位置和错误现象隔了两层。
# 这个自检把"第几个括号没闭合"直接说出来，比解析器的 "Unexpected token '}'" 近。
function Assert-Syntax {
    param([string] $Path)
    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors -and $parseErrors.Count -gt 0) {
        $first = $parseErrors[0]
        throw "启动脚本解析失败：$($first.Extent.StartLineNumber) 行 $($first.Message)。" +
              "PowerShell 先整份解析再执行，所以这里错了脚本一行都不会跑。"
    }
}
Assert-Syntax -Path $PSCommandPath

#  ---------------------------------------------------------------- Python 环境
# conda activate 在脚本里的行为和交互式 shell 不同（conda 只给交互式会话装了 hook），
# 所以这里先试 conda 的批处理入口，再用解释器路径兜底。
#
# 但**兜底必须能独立成立**，因为激活这条路会静默失败：`cmd /c "call conda.bat
# activate pytorch"` 里的 cmd 是子进程，它设的 CONDA_PREFIX 不会回传到 PowerShell。
# 于是脚本会拿到空的 CONDA_PREFIX，回退到 base 环境的 python——那个环境没有
# websockets，`python -m server` 直接 ModuleNotFoundError，而扩展侧看到的只是
# "后端启动超时"。这个 bug 存活了一轮，因为验证时 shell 里恰好已经有 conda 变量，
# 把空 CONDA_PREFIX 这条路径盖住了。
function Get-CondaEnvPython {
    # Interpreter for a named env, found from the env root, without activation.
    param([string] $Name)
    $roots = @()
    if ($env:CONDA_ROOT) { $roots += $env:CONDA_ROOT }
    if ($env:CONDA_PREFIX) { $roots += (Split-Path -Parent $env:CONDA_PREFIX) }
    $roots += @("D:\minanconda", "$env:USERPROFILE\miniconda3", "$env:USERPROFILE\anaconda3", "D:\miniconda3")
    foreach ($root in ($roots | Where-Object { $_ -and (Test-Path $_) })) {
        foreach ($candidate in @(
                (Join-Path $root "envs\$Name\python.exe"),
                (Join-Path $root "python.exe"))) {
            if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
        }
    }
    return $null
}

function Enable-CondaEnv {
    param([string] $Name)
    if ($env:CONDA_PREFIX -and (Split-Path -Leaf $env:CONDA_PREFIX) -eq $Name) { return }
    $conda = $null
    foreach ($candidate in @(
            "$env:USERPROFILE\miniconda3\condabin\conda.bat",
            "D:\minanconda\condabin\conda.bat",
            "D:\miniconda3\condabin\conda.bat")) {
        if (Test-Path $candidate) { $conda = $candidate; break }
    }
    if (-not $conda) {
        $found = Get-Command conda.exe -ErrorAction SilentlyContinue
        if ($found) { $conda = $found.Source }
    }
    if (-not $conda) {
        Write-Note "conda 不在 PATH 上，也没有已知的 condabin；跳过 activate，改用解释器路径"
        return
    }
    # conda.bat 不是可执行文件，直接 & 调用只会被记事本打开，所以走 cmd /c。
    # 注意：这条命令**不会**把 CONDA_PREFIX 带回来，见上面的注释。
    try {
        if ($conda -like "*.bat") { cmd.exe /c "call `"$conda`" activate $Name" | Out-Null }
        else { Invoke-Expression "& `"$conda`" shell.powershell activate $Name | Out-String" | Out-Null }
    } catch {
        Write-Note "conda activate $Name 失败（$($_.Exception.Message)）；改用解释器路径"
    }
}

$CondaName = "pytorch"
$PythonSource = "TRANSLATION_PYTHON env"
if (-not $Python) {
    if ($env:TRANSLATION_PYTHON) {
        $Python = $env:TRANSLATION_PYTHON
    } else {
        Enable-CondaEnv -Name $CondaName
        $PythonSource = "conda $CondaName (CONDA_PREFIX)"
        if (-not ($env:CONDA_PREFIX -and (Split-Path -Leaf $env:CONDA_PREFIX) -eq $CondaName)) {
            # Activation did not stick, so find the interpreter by path instead of
            # taking whatever `python` happens to resolve to. Falling back to the
            # PATH python is how the base env got picked and the backend died with
            # a missing module rather than a missing environment.
            $found = Get-CondaEnvPython -Name $CondaName
            if ($found) {
                $Python = $found
                $PythonSource = "conda $CondaName (by path)"
            } else {
                $Python = "D:\minanconda\envs\pytorch\python.exe"
                $PythonSource = "known path (conda not found)"
            }
        } else {
            $Python = Join-Path $env:CONDA_PREFIX "python.exe"
        }
    }
}
if (-not (Test-Path $Python)) {
    Write-Note "Python 解释器不存在：$Python（装依赖见 README「测试」一节）"
    exit 1
}
# 解释器必须真的能 import 服务端的依赖。少了这一步，"起不来"会表现为扩展侧的
# 90 秒超时，而不是这里的一行 ModuleNotFoundError。
#
# 这一段的 `$ErrorActionPreference = "Continue"` 是必须的：Preference 为 Stop 时，
# 原生命令写往 stderr 的第一行就会变成终止错误，于是打印出来的是 Python 的
# traceback 头部，而下面那句"缺依赖"永远轮不到执行。
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$depCheck = & $Python -c "import websockets, numpy; print('ok')" 2>&1 | Out-String
$depExit = $LASTEXITCODE
$ErrorActionPreference = $previousPreference
if ($depExit -ne 0) {
    Write-Note "Python 缺依赖（$Python）："
    Write-Note ($depCheck.Trim() -split "`n" | Select-Object -Last 3 | Out-String).Trim()
    Write-Note "这只解释器跑不了 server。用 -Python 指定 conda pytorch 环境里的解释器。"
    exit 1
}
Write-Note "python   $PythonSource  $Python"

# ------------------------------------------------------------ llama.cpp 版本
#
# 这段原来有自己的 Resolve-LlamaServer，和 scripts\resolve_llama.ps1 是两份解析，
# 而且方向相反：那份默认最新（要 -UsePinned 才回 2.47.0），这份默认 2.47.0
# （要 -AutoLatest 才回最新）。replay.ps1 调的是 resolve_llama.ps1，所以
# **测量一直落在 2.47.0 上，靠的不是谁钉了它，而是两条路恰好用了不同的解析器**。
# 任何一边改动都会让此前所有数字不可比，而日志里看不出发生过什么。
#
# 现在只有一份解析器：resolve_llama.ps1。不指定就是已装最新（shipping），
# 指定就是那一版，且**没装就报错，不退回最新**。
$LlamaPlatform = "win-x86_64-nvidia-cuda12-avx2"

$resolveArgs = @{}
if ($LlamaVersion) { $resolveArgs["Version"] = $LlamaVersion }

# -LlamaPath 是这条脚本自己的、绕过一整套解析的逃生口：它给的是一个 exe 的绝对
# 路径，不是版本号，而版本号要靠目录名才认得出来。测别的构建时用它。
if ($LlamaPath) {
    if (-not (Test-Path $LlamaPath)) { throw "指定的 llama-server.exe 不存在：$LlamaPath" }
    $leaf = Split-Path -Leaf (Split-Path -Parent $LlamaPath)
    $backend = [pscustomobject]@{
        path = $LlamaPath
        version = ($leaf -replace "^llama\.cpp-$LlamaPlatform-", "")
        how = "-LlamaPath（绕过版本解析）"
        installed = ""
    }
} else {
    $backend = & (Join-Path $PSScriptRoot "resolve_llama.ps1") @resolveArgs | Select-Object -Last 1
    if (-not $backend) { throw "resolve_llama.ps1 没有给出结果" }
}
$Llama = $backend.path
$LlamaIsCli = ($backend.is_cli -eq $true) -or ((Split-Path $Llama -Leaf) -match "^llama(\.exe)?$")
# 这一段是给人看的，不是给机器看的：llama-common.dll 等依赖在 exe 同目录，复制单文件跑不起来。
Write-Note "llama    $LlamaPlatform-$($backend.version) ($($backend.selected))"
if ($backend.installed) {
    Write-Note "llama    已装：$($backend.installed)（钉一版：runtime.json 的 llama_version，或 -LlamaVersion）"
}

# Which translator the Python side will talk to, decided the same way Python
# decides it (server/config.py MT_BACKEND). Read here rather than asked of Python
# because the port has to be known before Python starts -- and because a
# translator on the wrong port looks exactly like a translator that is not there.
# Which pipeline to run. A chain is the whole thing -- which models, which processes,
# which ports -- not a single recogniser. runtime.json holds the choice and its
# provenance; the extension writes it and then asks for a restart, so the value is a
# server-side property rather than a UI preference that only lives in chrome.storage.
#
# Read here rather than passed in, because start.ps1 is what the native host invokes
# with no arguments at all. An unreadable runtime.json falls back to the default chain
# and says so, rather than refusing to start: a corrupt preference should cost the
# preference, not the backend.
$Chain = if ($env:TRANSLATION_CHAIN) { $env:TRANSLATION_CHAIN } else { "" }
$Qwen3Port = 8099
if (Test-Path (Join-Path $Root "runtime.json")) {
    try {
        $runtimeForChain = Get-Content (Join-Path $Root "runtime.json") -Raw | ConvertFrom-Json
        if (-not $Chain -and $runtimeForChain.chain) { $Chain = $runtimeForChain.chain }
    } catch {
        Write-Note "runtime.json 的 chain 读不出来（$($_.Exception.Message)）；按默认链路继续"
    }
}
# The chain table is server/config.py, not runtime.json: runtime.json holds this
# machine's absolute paths and is gitignored, so a fresh clone has no chain table at all
# and no default either. Python is asked rather than the file parsed, so the table has
# one definition that both sides read.
if (-not $Chain) { $Chain = & $Python -c "from server.config import DEFAULT_CHAIN; print(DEFAULT_CHAIN)" }

$ChainInfo = & $Python -c "import json;from server.config import CHAINS;c=CHAINS.get('$Chain');print(json.dumps(c) if c else '')"
if (-not $ChainInfo) {
    $names = (& $Python -c "from server.config import CHAINS; print('、'.join(CHAINS))").Trim()
    throw "未知的链路：$Chain（已知：$names）。"
}
$ChainSpec = $ChainInfo | ConvertFrom-Json
# gemma-e2e is two switches rather than one: the translator becomes Gemma4 E2B and the
# recogniser disappears, because one model both hears and translates. Setting only one
# of them gives either the audio-native pipeline with a text-only translator or a
# whisper pipeline pointed at a Gemma4 endpoint, and neither is the thing the chain names.
# It was reachable before only by exporting both by hand, which is why the first version
# of this switch left it out -- the reason was a missing branch, not a missing pipeline.
# Record what is actually going to start, which is not always what was asked for: the
# qwen3 branch below falls back to whisper if the Qwen3 weights or the 2.53.0 build are
# missing, and a runtime.json that keeps saying qwen3-hy after that makes the dropdown
# name a chain the backend is not on. `chain` keeps what was requested, so the fallback
# still knows what to return to.
#
# Set here, before the branches, because the gemma-e2e branch has no fallback and so
# never reaches the else-branch copy. Assigning it inside the else meant gemma-e2e wrote
# a null chain_running, which the host then had to fall back to `chain` for -- the exact
# substitution this is meant to remove.
$ChainRunning = $Chain

if ($Chain -eq "gemma-e2e") {
    $env:TRANSLATION_MT_BACKEND = "gemma4"
    $env:TRANSLATION_AUDIO_NATIVE = "1"
    $MtPort = 8082
    $AsrBackend = ""
    Write-Note "chain    $Chain  (音频直出：Gemma4 E2B 同时听和译，8082)"
    Write-Note "         ⚠ 实测在干净旁白上可用，对白压在配乐下会崩（REPORT 25.3）"
} else {
    if ($ChainSpec.asr_backend) { $AsrBackend = $ChainSpec.asr_backend } else { $AsrBackend = "" }
    $Qwen3Port = if ($ChainSpec.qwen3_port) { [int]$ChainSpec.qwen3_port } else { $Qwen3Port }
    $env:TRANSLATION_ASR_BACKEND = $AsrBackend
    if ($env:TRANSLATION_MT_BACKEND -eq "gemma4") {
        # A previous gemma-e2e left these set in the environment this process inherited.
        $env:TRANSLATION_MT_BACKEND = "index-translate"
        $env:TRANSLATION_AUDIO_NATIVE = "0"
    }
    Write-Note "chain    $Chain  (asr=$AsrBackend, $($ChainSpec.status))"
}

$MtBackend = if ($env:TRANSLATION_MT_BACKEND) { $env:TRANSLATION_MT_BACKEND.ToLower() } else { "index-translate" }
$MtPort = if ($MtBackend -eq "gemma4") { 8082 } else { 8080 }
Write-Note "mt       $MtBackend  port $MtPort"

# Resolve the weights here, before any branch that might not need them.
#
# The lookup below used to sit in the hy-mt2 branch only, which meant the "llama-server
# already on 8080" path never saw it -- and that path is the one a restart takes, which
# is exactly when a stale server is serving a model that is no longer the one on disk.
# Ambiguity is a property of the directory, not of who is going to read the file, so it
# is checked here and the result is reused below.
$HyGguf = $null
if ($MtBackend -eq "index-translate") {
    $HyGguf = Get-IndexTranslateGguf
    if (-not $HyGguf) {
        $HyGguf = Get-HyMt2Gguf
    }
    Write-Note "weights  $(if ($HyGguf) { $HyGguf.Name } else { 'MISSING (Index-Translate Q4_K_M)' })"
} elseif ($MtBackend -ne "gemma4") {
    $HyGguf = Get-HyMt2Gguf
    Write-Note "weights  $(if ($HyGguf) { $HyGguf.Name } else { 'MISSING (hy-mt2 Q4_K_M)' })"
}

# -DryRun 停在进程启动之前：扩展点"开始"会拉起什么，这里能问出来而不必真的拉起来。
# 之前没有这个入口，唯一能问的途径是把后端真起一遍再读日志。
if ($DryRun) {
    foreach ($port in $MtPort, 8765) {
        try {
            $probe = New-Object System.Net.Sockets.TcpClient
            $probe.Connect("127.0.0.1", $port)
            $probe.Close()
            Write-Note "dry run：端口 $port 已被占用，实际启动会复用它"
        } catch {
            Write-Note "dry run：端口 $port 空闲，实际启动会占用它"
        }
    }
    exit 0
}

function Test-Port([int]$Port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $Port)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

if (Test-Port $MtPort) {
    Write-Note "llama-server already on 127.0.0.1:$MtPort ($MtBackend)"
} elseif ($env:TRANSLATION_AUDIO_NATIVE -eq "1") {
    # Audio-native pipeline: one model both hears and translates, so whisper and
    # the text model are not started at all.
    #
    # The projector stays on the CPU (--mmproj-device none), and the reason is
    # GPU contention -- not memory.
    #
    # It was originally set because the card was believed to be at 3774 of 4096
    # MiB with 320 to spare, leaving no room for a 532 MB projector. That figure
    # was wrong by half: nvidia-smi reads 1842, and the projector does fit on the
    # GPU with room over.
    #
    # Measured anyway, changing nothing else, interleaved, three rounds each:
    # projector on CPU   first packet at  920 ms, 1756 MiB
    # projector on GPU   first packet at 3442 ms, 2528 MiB
    # Same quality either way -- zero bad fragments in both -- so the 3.7x is
    # pure latency, not different output.
    #
    # The mechanism: the projector is the audio encoder, and on the GPU its
    # prefill queues behind the main model's compute instead of running beside
    # it. On the CPU the two overlap. So the encoder on the CPU is not a
    # bottleneck to be removed, it is free parallelism to be kept -- which is
    # also why speculative decoding measured faster here than expected.
    #
    # Do not move it to the GPU to "save memory". The GPU is where it lost.
    #
    # MTP: the 503 is version-dependent, and off is still right -- for a different
    # reason than before.
    #
    # On llama.cpp 2.47 every request carrying input_audio was answered 503 with the
    # draft loaded, so "turning TRANSLATION_MTP on makes audio translation fail
    # outright" was literally true. On 2.53 that is fixed: five audio fragments with
    # the draft loaded all returned 200 and the draft counters were non-zero
    # (0/30, 1/15, 2/18, 2/30, 3/12), so the draft does engage on the audio path now.
    #
    # The reason to leave it off is instead that it is a net loss here:
    #
    #     MTP on   (draft-mtp n_max=3)   49.6 tok/s   22/174 accepted (12.6%)
    #     MTP off                          59.2 tok/s   no draft counters exist
    #
    # 12.6% acceptance does not pay for the draft's own cost, so the audio path is
    # faster without it. Re-measure if a later build raises acceptance -- and check
    # the version before quoting the 503, because that finding has an expiry date.
    #
    # The older note that follows is kept because its error is still instructive:
    # reading "15.1 ms/token with the draft against 12.9 without, at the same 0.30
    # acceptance rate" as speculative decoding not helping could not support that
    # conclusion -- with the draft off this server reports no draft counters at all,
    # so there was no acceptance rate on that side to compare.
    #
    # The draft loads fully onto the GPU -- 5 of 5 layers -- so this was never a
    # memory problem.
    $gguf = Get-ChildItem "$Root\models\gemma4-e2b\translate*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
    $mmproj = Get-ChildItem "$Root\models\gemma4-e2b\*mmproj*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $gguf) {
        Write-Note "Audio model missing. Expected models\gemma4-e2b\translate*.gguf"
    } elseif (-not $mmproj) {
        Write-Note "Audio projector missing. Expected models\gemma4-e2b\*mmproj*.gguf"
    } elseif (-not (Test-Path $Llama)) {
        Write-Note "llama-server.exe not found: $Llama"
    } else {
        $args = @(
            "-m", $gguf.FullName,
            "--mmproj", $mmproj.FullName,
            "--mmproj-device", "none",
            "--host", "127.0.0.1",
            "--port", "8080",
            "-ngl", "99",
            "-c", "2048",
            "-t", "4",
            "--jinja",
            "-lv", "4"
        )
        if ($env:TRANSLATION_MTP -eq "1") {
            $draft = Get-ChildItem "$Root\models\gemma4-e2b\mtp-*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($draft) {
                $args += @("--model-draft", $draft.FullName, "--spec-type", "draft-mtp", "--spec-draft-n-max", "3")
                Write-Note "MTP draft enabled. On this path it is a net loss (49.6 vs 59.2 tok/s) -- see the note above."
            } else {
                Write-Note "MTP draft weights not found; running without"
            }
        }
        Write-Note "llama-server start: audio-native, projector on CPU"
        $launchArgs = if ($LlamaIsCli) { @("serve") + $args } else { $args }
        Start-Process -FilePath $Llama -WorkingDirectory $Root -ArgumentList $launchArgs `
            -RedirectStandardOutput (Join-Path $LogDir "llama.out") `
            -RedirectStandardError (Join-Path $LogDir "llama.err") -WindowStyle Hidden
    }
} elseif ($MtBackend -eq "gemma4") {
    # Gemma 4 E2B as a TEXT translator, on its own port.
    #
    # No --mmproj, and that is the requested shape rather than an omission: the
    # projector is the audio encoder, and leaving it out means this server can
    # only translate text. Whisper still does the recognising, so this is a
    # translation-model swap and not the audio-native branch wearing its weights.
    #
    # Three numbers are load-bearing and all three were measured on this card:
    #
    #   -ngl 32 of 36   the weights are 3037 MiB and the card is 4096. All 36
    #                   layers plus the 93 MiB draft do not fit, and pushing them
    #                   past the card spends the difference in shared memory,
    #                   which is slower than leaving the tail on the CPU on
    #                   purpose. At 32 the first token costs 110-494 ms and decode
    #                   runs 43-65 tok/s. All 36 with the projector on the CPU
    #                   (`--mmproj-device none`, -ngl 99) measures faster still --
    #                   75.4 tok/s on text translation -- which is why that is the
    #                   other configuration worth trying.
    #   -c 2048         a subtitle line plus injected context is well under this;
    #                   the KV cache is what would grow if it were not.
    #   --model-draft   the MTP head, 5/5 layers resident, and it engages on TEXT
    #                   requests (draft_n > 0, acceptance 0.19-0.5). On 2.47 it
    #                   made AUDIO requests fail with 503; on 2.53 it does not, but
    #                   it is still a net loss there (49.6 against 59.2 tok/s), so
    #                   the audio-native branch above leaves it off. See the note
    #                   in that branch for the version table.
    $mdir = Join-Path $Root "models\gemma4-e2b"
    $gguf = Join-Path $mdir "translate_gemma4_sub-E2B-Q4_K_XL.gguf"
    $draft = Join-Path $mdir "mtp-gemma-4-E2B-it-Q8_0.gguf"
    $ngl = if ($env:TRANSLATION_GEMMA_NGL) { $env:TRANSLATION_GEMMA_NGL } else { "32" }
    $ctx = if ($env:TRANSLATION_GEMMA_CTX) { $env:TRANSLATION_GEMMA_CTX } else { "2048" }
    if (-not (Test-Path $gguf)) {
        Write-Note "Gemma translator weights missing: $gguf"
    } elseif (-not (Test-Path $Llama)) {
        Write-Note "llama-server.exe not found: $Llama"
    } else {
        $args = @(
            "-m", $gguf,
            "--host", "127.0.0.1",
            "--port", "$MtPort",
            "-ngl", "$ngl",
            "-c", "$ctx",
            "-t", "4",
            "-np", "1",
            "-lv", "4"
        )
        if ($env:TRANSLATION_GEMMA_MTP -ne "0" -and (Test-Path $draft)) {
            $args += @("--model-draft", $draft, "--spec-type", "draft-mtp", "--spec-draft-n-max", "3")
            Write-Note "llama-server start: gemma4 translator, ngl=$ngl ctx=$ctx + MTP draft"
        } else {
            Write-Note "llama-server start: gemma4 translator, ngl=$ngl ctx=$ctx, no draft"
        }
        $launchArgs = if ($LlamaIsCli) { @("serve") + $args } else { $args }
        Start-Process -FilePath $Llama -WorkingDirectory $Root -ArgumentList $launchArgs `
            -RedirectStandardOutput (Join-Path $LogDir "llama.out") `
            -RedirectStandardError (Join-Path $LogDir "llama.err") -WindowStyle Hidden
    }
} else {
    # Already resolved and ambiguity already refused, above.
    $gguf = $HyGguf
    if (-not $gguf) {
        Write-Note "Hy-MT2 GGUF missing. Download: $Python scripts\download_models.py"
    } elseif (-not (Test-Path $Llama)) {
        Write-Note "llama-server.exe not found: $Llama"
    } else {
        $llamaCfg = & $Python -c "from server.config import resolve_llama_runtime; print(','.join(map(str, resolve_llama_runtime())))"
        $parts = $llamaCfg.Trim().Split(',')
        $ngl = [int]$parts[0]
        $ctx = [int]$parts[1]
        $vramNote = & $Python -c "import json;from server.config import RUNTIME_JSON;r=json.loads(RUNTIME_JSON.read_text(encoding='utf-8')) if RUNTIME_JSON.exists() else {};print('vram_used_mib=%s fit_target_mib=%s' % (r.get('vram_used_mib_after_load'), r.get('fit_target_mib')))"
        # Startup log keeps -ngl / "offloaded x/y layers" / n_slots visible after the window is gone.
        $LlamaOut = Join-Path $LogDir "llama.out"
        $LlamaErr = Join-Path $LogDir "llama.err"
        Write-Note "llama-server start: ngl=$ngl ctx=$ctx np=3 ($vramNote)"
        $llamaArgs = @(
            "-m", $gguf.FullName,
            "--host", "127.0.0.1",
            "--port", "8080",
            "-ngl", "$ngl",
            "-c", "$ctx",
            "-t", "4",
            "-np", "3",
            "--no-webui",
            # -lv 4 is the lowest level that prints
            # "load_tensors: offloaded 33/33 layers to GPU"; without it the
            # startup log never shows whether -ngl actually landed.
            "-lv", "4"
        )
        if ($gguf.Name -like "*Index*") {
            $llamaArgs += @("--reasoning", "off")
        }
        $launchArgs = if ($LlamaIsCli) { @("serve") + $llamaArgs } else { $llamaArgs }
        Start-Process -FilePath $Llama -WorkingDirectory $Root -ArgumentList $launchArgs `
            -RedirectStandardOutput $LlamaOut -RedirectStandardError $LlamaErr -WindowStyle Hidden
        Write-Note "starting llama-server $($gguf.Name)"

        # The Qwen3 recogniser needs a second llama-server, and this has to be here
        # rather than in the extension. The gap it fills is why: Qwen3AsrEngine posts to
        # 127.0.0.1:8099, and nothing used to start that port, so selecting the chain
        # without this would give a backend whose every recognition call hangs -- and
        # hangs without an exception, which is how the first attempt at this read as a
        # broken model rather than a missing process.
        #
        # Absolute paths, because Start-Process resolves a relative -m against
        # -WorkingDirectory, which is the backend install directory. That produced a
        # server that died on a model path it could not find.
        if ($AsrBackend -eq "qwen3") {
            # Resolved by the same code that resolves them for the server.
            #
            # This used to build the path here, against the repository root, while
            # server.config.resolve_models_dir() resolved the same files through a
            # chain that includes D:/TranslationDesktop/models. The two disagreed
            # and nothing said so: start.ps1 concluded the weights were missing and
            # silently fell back to whisper-hy, printing
            #
            #     chain    qwen3-hy  (asr=qwen3, ...)
            #     Qwen3-ASR 权重缺失 ... 改用 whisper
            #     chain    实际启动 whisper-hy
            #
            # so a run labelled qwen3-hy was whisper-hy, with no error and a
            # report that read correctly. Same shape as the twelve "MTP on" cells
            # that were all off.
            #
            # The env override stays, but it is now treated as an absolute path --
            # Join-Path concatenates rather than replaces, so Join-Path $Root
            # 'D:\...' produced 'C:\repo\D:\...' and the override could never work
            # for a model on another drive. There was no value that both worked and
            # matched what the server loads.
            $qwenPaths = & $Python -c "from server.config import QWEN3_ASR_MODEL, QWEN3_ASR_MMPROJ; print(QWEN3_ASR_MODEL); print(QWEN3_ASR_MMPROJ)"
            $qwenModel = if ($env:TRANSLATION_QWEN3_ASR_MODEL) { $env:TRANSLATION_QWEN3_ASR_MODEL } else { $qwenPaths[0] }
            $qwenMmproj = if ($env:TRANSLATION_QWEN3_ASR_MMPROJ) { $env:TRANSLATION_QWEN3_ASR_MMPROJ } else { $qwenPaths[1] }
            # 2.53.0 is the only installed build with the multimodal projector path this
            # needs. It is resolved by absolute path so this one is pinned regardless of
            # what $Llama is.
            #
            # The original comment here said "hy-mt2 stays on 2.47.0, which is what every
            # number in REPORT.md was measured on, so the two run different builds on
            # purpose." That described an intent the code did not implement: hy-mt2 is
            # started with $Llama, which is the newest installed build unless a version is
            # named, so hy-mt2 was running 2.53.0 too. REPORT 44 keeps the error --
            # the start.log line that said "newest installed" was right there.
            $llama53 = Get-ChildItem "$env:USERPROFILE\.lmstudio\extensions\backends\llama.cpp-$LlamaPlatform-2.53.0\llama-server.exe" -ErrorAction SilentlyContinue |
                Select-Object -First 1
            $qwenExe = if ($llama53) { $llama53.FullName } else { $Llama }
            if (-not (Test-Path $qwenModel) -or -not (Test-Path $qwenMmproj)) {
                throw "Qwen3-ASR 权重缺失（$qwenModel / $qwenMmproj）。Fail-Fast 原则：拒绝静默降级为 whisper，请检查 D:\TranslationDesktop\models\Qwen-ASR-0.6B"
            } elseif (-not $qwenExe -or -not (Test-Path $qwenExe)) {
                throw "找不到可用的 llama 引擎（Qwen3-ASR 需要支持 mmproj 的 llama-server 或 llama.exe）。"
            } elseif (Test-Port $Qwen3Port) {
                $qwenHealthy = $false
                try {
                    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Qwen3Port/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction SilentlyContinue
                    if ($resp.StatusCode -eq 200) { $qwenHealthy = $true }
                } catch {}
                if ($qwenHealthy) {
                    Write-Note "端口 $Qwen3Port 已被占用且健康，沿用现有的 Qwen3-ASR 服务"
                } else {
                    Write-Note "端口 $Qwen3Port 已被占用但未响应健康检查，正在杀死并重新拉起..."
                    try {
                        $lines = & netstat -ano -p TCP | Select-String ":$Qwen3Port\s+.*LISTENING"
                        foreach ($l in $lines) {
                            $parts = ($l.Line.Trim() -split "\s+")
                            $pidToKill = [int]$parts[-1]
                            if ($pidToKill) { taskkill.exe /PID $pidToKill /T /F 2>&1 | Out-Null }
                        }
                    } catch {}
                    Start-Sleep -Milliseconds 500
                    # Proceed to launch
                    Write-Note "llama-server start: Qwen3-ASR on $Qwen3Port ($(Split-Path $qwenExe -Leaf), -np 2, -c 4096)"
                    $qwenIsCli = ((Split-Path $qwenExe -Leaf) -match "^llama(\.exe)?$")
                    $qwenLaunchArgs = if ($qwenIsCli) { @("serve") } else { @() }
                    $qwenLaunchArgs += @(
                        "-m", $qwenModel,
                        "--mmproj", $qwenMmproj,
                        "--host", "127.0.0.1",
                        "--port", "$Qwen3Port",
                        "-ngl", "99",
                        "-c", "4096",
                        "-t", "4",
                        "-np", "2"
                    )
                    Start-Process -FilePath $qwenExe -WorkingDirectory $Root -ArgumentList $qwenLaunchArgs `
                       -RedirectStandardOutput (Join-Path $LogDir "llama8099.out") `
                       -RedirectStandardError (Join-Path $LogDir "llama8099.err") -WindowStyle Hidden
                    $qwenReady = $false
                    for ($i = 0; $i -lt 30; $i++) {
                        Start-Sleep -Seconds 1
                        if (Test-Port $Qwen3Port) { $qwenReady = $true; break }
                    }
                    if ($qwenReady) {
                        Write-Note "Qwen3-ASR ready on $Qwen3Port"
                    } else {
                        try {
                            $lines = & netstat -ano -p TCP | Select-String ":$Qwen3Port\s+.*LISTENING"
                            foreach ($l in $lines) {
                                $parts = ($l.Line.Trim() -split "\s+")
                                $pidToKill = [int]$parts[-1]
                                if ($pidToKill) { taskkill.exe /PID $pidToKill /T /F 2>&1 | Out-Null }
                            }
                        } catch {}
                        throw "Qwen3-ASR 在 30 秒内未就绪（端口 $Qwen3Port）。Fail-Fast 原则：启动中止，已清理显存，日志在 $LogDir\llama8099.err"
                    }
                }
            } else {
                # -np 1. Measured: with four slots the 30-second repetition events were
                # still bounded to the same 128-token cap, so slots were not the trigger,
                # but one slot removes the concurrency the pipeline does not need anyway.
                Write-Note "llama-server start: Qwen3-ASR on $Qwen3Port ($(Split-Path $qwenExe -Leaf), -np 2, -c 4096)"
                $qwenIsCli = ((Split-Path $qwenExe -Leaf) -match "^llama(\.exe)?$")
                $qwenLaunchArgs = if ($qwenIsCli) { @("serve") } else { @() }
                $qwenLaunchArgs += @(
                    "-m", $qwenModel,
                    "--mmproj", $qwenMmproj,
                    "--host", "127.0.0.1",
                    "--port", "$Qwen3Port",
                    "-ngl", "99",
                    "-c", "4096",
                    "-t", "4",
                    "-np", "2"
                )
                Start-Process -FilePath $qwenExe -WorkingDirectory $Root -ArgumentList $qwenLaunchArgs `
                   -RedirectStandardOutput (Join-Path $LogDir "llama8099.out") `
                   -RedirectStandardError (Join-Path $LogDir "llama8099.err") -WindowStyle Hidden
                # Poll rather than sleep. Loading the audio tower takes longer than the
                # python service does, so a fixed wait would hand over a half-ready
                # recogniser and the first three recognitions would be slow for no
                # reason a reader could see.
                $qwenReady = $false
                for ($i = 0; $i -lt 30; $i++) {
                    Start-Sleep -Seconds 1
                    if (Test-Port $Qwen3Port) { $qwenReady = $true; break }
                }
                if ($qwenReady) {
                    Write-Note "Qwen3-ASR ready on $Qwen3Port"
                } else {
                    try {
                        $lines = & netstat -ano -p TCP | Select-String ":$Qwen3Port\s+.*LISTENING"
                        foreach ($l in $lines) {
                            $parts = ($l.Line.Trim() -split "\s+")
                            $pidToKill = [int]$parts[-1]
                            if ($pidToKill) { taskkill.exe /PID $pidToKill /T /F 2>&1 | Out-Null }
                        }
                    } catch {}
                    throw "Qwen3-ASR 在 30 秒内未就绪（端口 $Qwen3Port）。Fail-Fast 原则：启动中止，已清理显存，日志在 $LogDir\llama8099.err"
                }
            }
        } else {
            # 非 Qwen 模式（Nemotron / Whisper）：释放端口 8099 上的旧 Qwen 服务以腾出全部 GPU 显存
            if (Test-Port $Qwen3Port) {
                Write-Note "非 Qwen 模式：释放端口 $Qwen3Port 上的旧服务以腾出显存..."
                try {
                    $lines = & netstat -ano -p TCP | Select-String ":$Qwen3Port\s+.*LISTENING"
                    foreach ($l in $lines) {
                        $parts = ($l.Line.Trim() -split "\s+")
                        $pidToKill = [int]$parts[-1]
                        if ($pidToKill) { taskkill.exe /PID $pidToKill /T /F 2>&1 | Out-Null }
                    }
                } catch {}
            }
        }
    }
}

# TRANSLATION_AUDIO_NATIVE=1 is read by server/ws_server.py, which then skips
# loading the recogniser entirely.
# Record the chain that is actually going to run, which the dropdown reads so it
# cannot name a chain the backend is not on. Written after the fallbacks above and
# before the service starts, because that is the last moment the answer is known.
# `chain` keeps what was asked for, so a fallback still knows what to return to.
try {
    $rtPath = Join-Path $Root "runtime.json"
    # utf-8 without a BOM, explicitly. PS 5.1's "UTF8" *is* UTF8 with a BOM, and a
    # BOM makes json.loads fail on a strict utf-8 read -- so writing it this way
    # silently broke the host's own read_chain(), which then answered with the
    # default and made the dropdown name a chain nothing was running. Same trap as
    # the native host manifest in install_native_host.ps1.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $rt = Get-Content $rtPath -Raw | ConvertFrom-Json
    $rt | Add-Member -NotePropertyName chain_running -NotePropertyValue $ChainRunning -Force
    [System.IO.File]::WriteAllText($rtPath, ($rt | ConvertTo-Json -Depth 6), $utf8NoBom)
} catch {
    Write-Note "runtime.json 的 chain_running 写不进去（$($_.Exception.Message)）；下拉会继续显示请求的链路"
}

# 把"实际在跑的路线"也交给进程环境。
#
# 输出方式是**路线的属性**（server/config.py 的 CHAINS[...]["output"]），而字幕窗、服务端
# 都是另一个进程：它们要能自己查出这条路线声明的是哪一种，而不是自己比较识别器的名字。
#
# $ChainRunning 是**观测到的**值 —— 上面的回退分支改过它。用请求值会让"请求 qwen3-hy
# 但回退到 whisper-hy"那次仍自称 qwen3，正是 AGENT §4 说的"臂的身份只能来自观测状态"。
$env:TRANSLATION_CHAIN_RUNNING = $ChainRunning

$env:PYTHONUNBUFFERED = "1"
& $Python -m server
