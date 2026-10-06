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

$env:PYTHONUNBUFFERED = "1"
& $Python -m server
