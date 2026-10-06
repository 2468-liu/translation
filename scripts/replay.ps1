param(
    [Parameter(Mandatory = $true)][string]$Name,
    [int]$StartupWait = 12
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
Start-Sleep -Seconds 2

# Files are locked while the server runs, so rotation has to follow the kill.
$rotate = @(
    @("logs\perf.log", "logs\perf.$Name.log"),
    @("logs\perf_caption.log", "logs\perf_caption.$Name.log"),
    @("logs\blog_3min_test.json", "logs\blog_3min_test.$Name.json")
)
foreach ($pair in $rotate) {
    if (Test-Path $pair[0]) {
        Move-Item -Force $pair[0] $pair[1]
        Write-Host "rotated $($pair[0]) -> $($pair[1])"
    }
}

$env:PYTHONUNBUFFERED = "1"
$env:TRANSLATION_PERF = "1"
Remove-Item Env:TRANSLATION_PERF_LOG -ErrorAction SilentlyContinue

Write-Host "starting server"
Start-Process -FilePath $Python -ArgumentList "-m", "server" -NoNewWindow | Out-Null
Start-Sleep -Seconds $StartupWait

$listening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if (-not $listening) { throw "server did not come up on 8765" }
Write-Host "server pid $($listening.OwningProcess)"

Write-Host "replaying samples/blog_3min.mp4 (~3.5 min)"
& $Python scripts\blog_video_test.py play

Write-Host ""
& $Python scripts\perf_compare.py $Name logs\perf.log logs\perf_caption.log logs\blog_3min_test.json