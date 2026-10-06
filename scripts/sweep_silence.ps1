<#
.SYNOPSIS
    Run the closing-silence sweep: N replays per threshold, level set between runs.

.DESCRIPTION
    Latency here is paid per fragment, so the closing silence is a fixed cost on
    every sentence and the question is where the gain stops being worth the
    sentences it cuts. Three replays per level, because one replay of this
    material has been seen to differ from itself by a factor of two.

    The threshold lives in server/config.py, so it is written before each level
    and the level name is carried in the run name. Changing it while a replay is
    in flight would let the next run inherit the previous level's setting under
    the wrong label, which is why the edit and the replay are one step here.

.EXAMPLE
    .\scripts\sweep_silence.ps1 -Runs 3 -Levels 550
    .\scripts\sweep_silence.ps1 -Runs 3 -Levels 400,550,700,1000
#>
[CmdletBinding()]
param(
    [string[]] $Levels = @("400", "550", "700"),
    [int] $Runs = 3,
    [string] $Config = "server\config.py"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot) -ErrorAction SilentlyContinue
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

function Set-MinSilence([int] $Ms) {
    if (-not (Test-Path $Config)) { throw "missing $Config" }
    $text = Get-Content $Config -Raw
    $next = [regex]::Replace($text, "GEMMA_MIN_SILENCE_MS = \d+", "GEMMA_MIN_SILENCE_MS = $Ms")
    if ($next -eq $text) { throw "GEMMA_MIN_SILENCE_MS not found in $Config" }
    Set-Content $Config -Value $next -Encoding utf8 -NoNewline
    # Read it back rather than trusting the write: a sweep that silently ran the
    # wrong threshold would look exactly like a sweep that found no difference.
    $seen = [regex]::Match((Get-Content $Config -Raw), "GEMMA_MIN_SILENCE_MS = (\d+)").Groups[1].Value
    if ($seen -ne "$Ms") { throw "wanted $Ms, config reads $seen" }
    Write-Host ""
    Write-Host "### GEMMA_MIN_SILENCE_MS = $Ms (confirmed)"
}

$failures = @()
foreach ($level in $Levels) {
    Set-MinSilence ([int] $level)
    for ($i = 1; $i -le $Runs; $i++) {
        $suffix = [char]([int][char]'a' + $i - 1)
        $name = "sil$level$suffix"
        Write-Host ""
        Write-Host "===== $name ====="
        try {
            & .\scripts\replay.ps1 $name -AudioNative *>&1 |
                Where-Object { $_ -notmatch "^audio in:|SoundcardRuntimeWarning|warnings\.warn|^\s+\d+\.\d+s translation:|^ASR|^VAD:|^WebSocket|^pipeline:|^server pid|^events=" }
            if ($LASTEXITCODE -ne 0) { $failures += $name }
        }
        catch {
            Write-Warning "$name failed: $_"
            $failures += $name
        }
    }
}

Write-Host ""
if ($failures.Count) {
    Write-Warning "incomplete: $($failures -join ', ')"
    Write-Warning "these levels have fewer than $Runs runs; report them as such"
}
else {
    Write-Host "all levels have $Runs runs"
}
