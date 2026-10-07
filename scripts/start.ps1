$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "D:\minanconda\envs\pytorch\python.exe"
$Llama = "C:\Users\20693\.lmstudio\extensions\backends\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.47.0\llama-server.exe"

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

if (Test-Port 8080) {
    Write-Host "llama-server already on 127.0.0.1:8080"
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
    # MTP is off, and enabling it breaks audio requests rather than slowing them.
    #
    # The earlier reason was wrong. It read "15.1 ms/token with the draft against
    # 12.9 without, at the same 0.30 acceptance rate" as speculative decoding not
    # helping audio input. That measurement could not support the conclusion:
    # with the draft off this server reports no draft counters at all, so there
    # was no acceptance rate on that side to compare, and with the draft on every
    # request carrying input_audio is answered 503.
    #
    # The draft does load, and fully onto the GPU -- 5 of 5 layers, 78 MiB -- so
    # this is not a memory problem. scripts/probe_mtp.py shows 13.0-13.1 ms/token
    # with it off. Leave TRANSLATION_MTP alone unless the 503 goes away; turning
    # it on makes audio translation fail outright.
    $gguf = Get-ChildItem "$Root\models\gemma4-e2b\translate*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
    $mmproj = Get-ChildItem "$Root\models\gemma4-e2b\*mmproj*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $gguf) {
        Write-Host "Audio model missing. Expected models\gemma4-e2b\translate*.gguf"
    } elseif (-not $mmproj) {
        Write-Host "Audio projector missing. Expected models\gemma4-e2b\*mmproj*.gguf"
    } elseif (-not (Test-Path $Llama)) {
        Write-Host "llama-server.exe not found: $Llama"
    } else {
        $LogDir = Join-Path $Root "logs"
        if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
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
                Write-Warning "MTP draft enabled. Audio requests are answered 503 with it on -- see the note above."
            }
            } else {
                Write-Host "MTP draft weights not found; running without"
            }
        }
        Write-Host "llama-server start: audio-native, projector on CPU"
        Start-Process -FilePath $Llama -WorkingDirectory (Split-Path $Llama) -ArgumentList $args `
            -RedirectStandardOutput (Join-Path $LogDir "llama.out") `
            -RedirectStandardError (Join-Path $LogDir "llama.err")
    }
} else {
    $gguf = Get-ChildItem "$Root\models\hy-mt2-1.8b\*Q4_K_M*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $gguf) {
        Write-Host "Hy-MT2 GGUF missing. Download: $Python scripts\download_models.py"
    } elseif (-not (Test-Path $Llama)) {
        Write-Host "llama-server.exe not found: $Llama"
    } else {
        $llamaCfg = & $Python -c "from server.config import resolve_llama_runtime; print(','.join(map(str, resolve_llama_runtime())))"
        $parts = $llamaCfg.Trim().Split(',')
        $ngl = [int]$parts[0]
        $ctx = [int]$parts[1]
        $vramNote = & $Python -c "import json;from server.config import RUNTIME_JSON;r=json.loads(RUNTIME_JSON.read_text(encoding='utf-8')) if RUNTIME_JSON.exists() else {};print('vram_used_mib=%s fit_target_mib=%s' % (r.get('vram_used_mib_after_load'), r.get('fit_target_mib')))"
        $LogDir = Join-Path $Root "logs"
        if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
        # Startup log keeps -ngl / "offloaded x/y layers" / n_slots visible after the window is gone.
        $LlamaOut = Join-Path $LogDir "llama.out"
        $LlamaErr = Join-Path $LogDir "llama.err"
        Write-Host "llama-server start: ngl=$ngl ctx=$ctx np=1 ($vramNote)"
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
        Write-Host "starting llama-server $($gguf.Name)"
    }
}

# TRANSLATION_AUDIO_NATIVE=1 is read by server/ws_server.py, which then skips
# loading the recogniser entirely.
$env:PYTHONUNBUFFERED = "1"
& $Python -m server
