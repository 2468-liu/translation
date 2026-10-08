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
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -UsePinned
#>
[CmdletBinding()]
param(
    # 回到 REPORT.md 里测过的那一版（runtime.json 的 llama_pinned_path，缺省 2.47.0）。
    [switch] $UsePinned,
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
# 平台的三个字段都重要：同号的 Vulkan / CPU 构建不是同一个程序，只按版本号挑会在
# 什么都没变的情况下换掉跑的东西。
$LlamaPlatform = "win-x86_64-nvidia-cuda12-avx2"
$BackendsRoot = Join-Path $env:USERPROFILE ".lmstudio\extensions\backends"

function Resolve-LlamaServer {
    # 1. 显式路径：参数 > 环境变量。
    foreach ($explicit in @($LlamaPath, $env:TRANSLATION_LLAMA_PATH)) {
        if ($explicit) {
            if (-not (Test-Path $explicit)) { throw "指定的 llama-server.exe 不存在：$explicit" }
            return [pscustomobject]@{ path = $explicit; version = "explicit"; how = "explicit" }
        }
    }

    # 2. 固定版：runtime.json 里 resolve_llama.ps1 -Pin 写下的那一版。
    if ($UsePinned -or $env:TRANSLATION_LLAMA_VERSION) {
        $pinned = $null
        if (Test-Path (Join-Path $Root "runtime.json")) {
            try {
                $runtime = Get-Content (Join-Path $Root "runtime.json") -Raw | ConvertFrom-Json
                if ($runtime.llama_pinned_path) { $pinned = $runtime.llama_pinned_path }
            } catch {
                Write-Note "runtime.json 读不出来（$($_.Exception.Message)）；按固定版缺省继续"
            }
        }
        $want = if ($env:TRANSLATION_LLAMA_VERSION) { $env:TRANSLATION_LLAMA_VERSION } else { "2.47.0" }
        if (-not $pinned) {
            $pinned = Join-Path $BackendsRoot "llama.cpp-$LlamaPlatform-$want\llama-server.exe"
        }
        if (-not (Test-Path $pinned)) { throw "固定版 llama-server.exe 不存在：$pinned" }
        # 版本号从路径里剥平台前缀，而不是原样打印目录名——日志里那一行是要被读的，
        # 而 "llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.47.0" 混在版本字段里就是噪声。
        $version = Split-Path -Leaf (Split-Path -Parent $pinned)
        $version = $version -replace "^llama\.cpp-$LlamaPlatform-", ""
        return [pscustomobject]@{ path = $pinned; version = $version; how = "pinned" }
    }

    # 3. 本机已装的最新一版。
    #
    # 排序用的是 [Version]，不是字符串：2.52.0 必须赢过 2.9.0，字符串比较会反过来。
    # 从目录名取版本，所以事实来自文件系统本身，不来自这里的常量。
    if (Test-Path $BackendsRoot) {
        $installed = @(Get-ChildItem $BackendsRoot -Directory -Filter "llama.cpp-$LlamaPlatform-*" | ForEach-Object {
                $exe = Join-Path $_.FullName "llama-server.exe"
                if (-not (Test-Path $exe)) { return }
                $version = $_.Name -replace "^llama\.cpp-$LlamaPlatform-", ""
                $key = try { [Version]$version } catch { [Version]"0.0.0" }
                [pscustomobject]@{ path = $exe; version = $version; key = $key; how = "newest installed" }
            } | Sort-Object key)
        if ($installed.Count -gt 0) {
            $chosen = $installed[-1]
            return [pscustomobject]@{
                path      = $chosen.path
                version   = $chosen.version
                how       = $chosen.how
                installed = ($installed | ForEach-Object { $_.version }) -join ", "
            }
        }
    }

    # 4. 一个都没找到：退回已知路径，让下面的存在性检查给出可读的错。
    return [pscustomobject]@{
        path    = Join-Path $BackendsRoot "llama.cpp-$LlamaPlatform-2.47.0\llama-server.exe"
        version = "2.47.0"
        how     = "fallback, none installed"
    }
}

$backend = Resolve-LlamaServer
$Llama = $backend.path
# 这一段是给人看的，不是给机器看的：llama-common.dll 等依赖在 exe 同目录，复制单文件跑不起来。
Write-Note "llama    $LlamaPlatform-$($backend.version) ($($backend.how))"
if ($backend.installed) { Write-Note "llama    已装：$($backend.installed)（改回固定版：-UsePinned）" }

# Which translator the Python side will talk to, decided the same way Python
# decides it (server/config.py MT_BACKEND). Read here rather than asked of Python
# because the port has to be known before Python starts -- and because a
# translator on the wrong port looks exactly like a translator that is not there.
$MtBackend = if ($env:TRANSLATION_MT_BACKEND) { $env:TRANSLATION_MT_BACKEND.ToLower() } else { "hy-mt2" }
$MtPort = if ($MtBackend -eq "gemma4") { 8082 } else { 8080 }
Write-Note "mt       $MtBackend  port $MtPort"

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
        Start-Process -FilePath $Llama -WorkingDirectory (Split-Path $Llama) -ArgumentList $args `
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
        Start-Process -FilePath $Llama -WorkingDirectory (Split-Path $Llama) -ArgumentList $args `
            -RedirectStandardOutput (Join-Path $LogDir "llama.out") `
            -RedirectStandardError (Join-Path $LogDir "llama.err") -WindowStyle Hidden
    }
} else {
    $gguf = Get-ChildItem "$Root\models\hy-mt2-1.8b\*Q4_K_M*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
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
        Write-Note "llama-server start: ngl=$ngl ctx=$ctx np=1 ($vramNote)"
        Start-Process -FilePath $Llama -WorkingDirectory (Split-Path $Llama) -ArgumentList @(
            "-m", $gguf.FullName,
            "--host", "127.0.0.1",
            "--port", "8080",
            "-ngl", "$ngl",
            "-c", "$ctx",
            "-t", "4",
            "-np", "1",
            # -lv 4 is the lowest level that prints
            # "load_tensors: offloaded 33/33 layers to GPU"; without it the
            # startup log never shows whether -ngl actually landed.
            "-lv", "4"
        ) -RedirectStandardOutput $LlamaOut -RedirectStandardError $LlamaErr -WindowStyle Hidden
        Write-Note "starting llama-server $($gguf.Name)"
    }
}

# TRANSLATION_AUDIO_NATIVE=1 is read by server/ws_server.py, which then skips
# loading the recogniser entirely.
$env:PYTHONUNBUFFERED = "1"
& $Python -m server
