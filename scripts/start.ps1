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
    # The projector stays on the CPU (--mmproj-device none). That is what makes
    # this fit a 4GB card, and it was measured at 3774 MiB of 4096 with no
    # measurable quality loss.
    #
    # MTP is deliberately off. Measured on an audio request: 15.1 ms/token with
    # the draft model against 12.9 without, at the same 0.30 acceptance rate --
    # speculative decoding does not help audio input, it only costs time. Enable
    # with TRANSLATION_MTP=1 once draft weights are present.
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
                Write-Host "MTP draft: enabled (no measured gain on audio)"
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
