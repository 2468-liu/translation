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
        Start-Process -FilePath $Llama -WorkingDirectory (Split-Path $Llama) -ArgumentList @(
            "-m", $gguf.FullName,
            "--host", "127.0.0.1",
            "--port", "8080",
            "-ngl", "99",
            "-c", "1024",
            "-t", "4",
            "-np", "1"
        ) -WindowStyle Hidden
        Write-Host "starting llama-server $($gguf.Name)"
    }
}

$env:PYTHONUNBUFFERED = "1"
& $Python -m server
