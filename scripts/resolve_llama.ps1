<#
.SYNOPSIS
    Resolve which llama.cpp backend to run, and report which one it picked.

.DESCRIPTION
    LM Studio keeps several builds side by side under
    %USERPROFILE%\.lmstudio\extensions\backends\, named
    llama.cpp-<platform>-<version>. The version is a sortable number, not a
    date, and which one is "latest" changes whenever LM Studio is updated.

    Pinned rather than floating, deliberately. The measurements in REPORT.md were
    taken on one build -- nvidia-cuda12-avx2-2.47.0 -- and a sweep that silently
    switched backends mid-run would make two arms incomparable for the same
    reason two config.py edits used to: nothing in the logs says what changed.
    So the default is the pinned build, TRANSLATION_LLAMA_VERSION or
    TRANSLATION_LLAMA_PATH overrides it, and -AutoLatest opts in to the newest
    installed one.

.EXAMPLE
    .\scripts\resolve_llama.ps1
    .\scripts\resolve_llama.ps1 -AutoLatest
    .\scripts\resolve_llama.ps1 -Pin 2.52.0
#>
[CmdletBinding()]
param(
    # Use the newest installed build for this platform instead of the pin.
    [switch] $AutoLatest,
    # Record this version as the pin.
    [switch] $Pin
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backends = Join-Path $env:USERPROFILE ".lmstudio\extensions\backends"
$PinFile = Join-Path $Root "runtime.json"

# The platform matters as much as the version: a Vulkan or CPU build of the same
# number is not the same program, and picking one of those by version alone would
# change what runs without changing anything a reader can see.
$Platform = "win-x86_64-nvidia-cuda12-avx2"
$Pinned = "2.47.0"

if ($env:TRANSLATION_LLAMA_PATH) {
    [pscustomobject]@{ path = $env:TRANSLATION_LLAMA_PATH; version = "explicit"; platform = $env:TRANSLATION_LLAMA_PATH }
    return
}

function Get-Installed {
    if (-not (Test-Path $Backends)) { return @() }
    Get-ChildItem $Backends -Directory -Filter "llama.cpp-$Platform-*" |
        ForEach-Object {
            $exe = Join-Path $_.FullName "llama-server.exe"
            if (-not (Test-Path $exe)) { return }
            $version = $_.Name -replace "^llama\.cpp-$Platform-", ""
            [pscustomobject]@{
                version = $version
                # Sort key: 2.52.0 must beat 2.9.0, which string compare gets wrong.
                key = [Version]$version
                path = $exe
            }
        }
}

$installed = @(Get-Installed)
if (-not $installed) {
    throw "no llama.cpp-$Platform-* build found under $Backends"
}

if ($Pin) {
    $newest = $installed | Sort-Object key | Select-Object -Last 1
    $cfg = @{}
    if (Test-Path $PinFile) { $cfg = Get-Content $PinFile -Raw | ConvertFrom-Json | ConvertTo-Json -Depth 8 | ConvertFrom-Json }
    $cfg | Add-Member -NotePropertyName llama_platform -NotePropertyValue $Platform -Force
    $cfg | Add-Member -NotePropertyName llama_pinned_version -NotePropertyValue $newest.version -Force
    $cfg | Add-Member -NotePropertyName llama_pinned_path -NotePropertyValue $newest.path -Force
    $cfg | Add-Member -NotePropertyName llama_pinned_when -NotePropertyValue (Get-Date).ToString("o") -Force
    $cfg | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $PinFile
    Write-Host "pinned llama.cpp $Platform-$($newest.version) in $PinFile"
}

$chosen = $null
$how = "pinned"
if ($AutoLatest) {
    $chosen = $installed | Sort-Object key | Select-Object -Last 1
    $how = "newest installed"
}
elseif ($env:TRANSLATION_LLAMA_VERSION) {
    $chosen = $installed | Where-Object { $_.version -eq $env:TRANSLATION_LLAMA_VERSION } | Select-Object -First 1
    if (-not $chosen) { throw "TRANSLATION_LLAMA_VERSION=$($env:TRANSLATION_LLAMA_VERSION) is not installed" }
    $how = "environment"
}
else {
    $chosen = $installed | Where-Object { $_.version -eq $Pinned } | Select-Object -First 1
    if (-not $chosen) {
        $chosen = $installed | Sort-Object key | Select-Object -Last 1
        $how = "pin missing, fell back to newest"
    }
}

$others = ($installed | Sort-Object key | ForEach-Object { $_.version }) -join ", "
Write-Host "# llama.cpp $Platform-$($chosen.version) ($how); installed: $others"
[pscustomobject]@{
    path     = $chosen.path
    version  = $chosen.version
    platform = $Platform
    selected = $how
    installed = $others
}
