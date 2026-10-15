<#
.SYNOPSIS
    Resolve which llama.cpp backend to run, and report which one it picked.

.DESCRIPTION
    LM Studio keeps several builds side by side under
    %USERPROFILE%\.lmstudio\extensions\backends\, named
    llama.cpp-<platform>-<version>. The version is a sortable number, not a
    date, and which one is "latest" changes whenever LM Studio is updated.

    This file is the only place that answers that question. It used to be one of
    two, and the two disagreed: start.ps1 had its own Resolve-LlamaServer that
    defaulted to newest installed behind -UsePinned, while this one defaulted to a
    hardcoded 2.47.0 behind -AutoLatest. replay.ps1 called this one, so every
    measurement landed on 2.47.0 -- not because anything pinned them, but because
    the measuring path and the running path resolved the same question two
    different ways. The next edit to either would have made every earlier number
    incomparable with no change to any arm's name.

    So: one resolver, one variable, two states. Unspecified means the newest build
    installed for this platform, which is what shipping uses, because 体感 is what
    is being optimised and some models need a newer build to load at all.
    Specified means exactly that version, in this order:

        -Version 2.52.0          argument        (a single deliberate run)
        $env:TRANSLATION_LLAMA_VERSION          (same, for a child process)
        runtime.json  llama_version             (this machine's standing choice)
        (nothing)                                newest installed

    A named version that is not installed raises. It does not fall back. The
    fallback this file used to have -- "pin missing, fell back to newest" -- is the
    failure it was written to prevent, appearing inside the file written to prevent
    it: an arm would run on a different build, the meta block would honestly record
    which build, and the two runs would still be incomparable to each other while
    looking fine.

.EXAMPLE
    .\scripts\resolve_llama.ps1
    .\scripts\resolve_llama.ps1 -Version 2.47.0
    Set-Content runtime.json  {"llama_version": "2.47.0"}   # this machine measures here
#>
[CmdletBinding()]
param(
    # Use exactly this llama.cpp version. Overrides the environment, runtime.json and
    # the newest-installed default, in that it outranks all three.
    [string] $Version
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backends = Join-Path $env:USERPROFILE ".lmstudio\extensions\backends"

# The platform matters as much as the version: a Vulkan or CPU build of the same
# number is not the same program, and picking one of those by version alone would
# change what runs without changing anything a reader can see.
$Platform = "win-x86_64-nvidia-cuda12-avx2"

function Get-Installed {
    $list = @()

    # 1. Custom environment variable override
    $custom = if ($env:TRANSLATION_LLAMA_EXE) { $env:TRANSLATION_LLAMA_EXE } else { $env:TRANSLATION_LLAMA_SERVER }
    if ($custom -and (Test-Path $custom)) {
        $resolved = (Resolve-Path $custom).Path
        $list += [pscustomobject]@{
            version = "custom"
            key     = [Version]"9999.0.0"
            path    = $resolved
            name    = (Split-Path $resolved -Leaf)
        }
    }

    # 2. System PATH candidates (official llama CLI or standalone llama-server)
    $pathCandidates = @("llama.exe", "llama-server.exe", "llama", "llama-server")
    foreach ($cand in $pathCandidates) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
            $ver = "official-cli"
            $sortKey = [Version]"999.0.0"
            try {
                $verOut = & $cmd.Source --version 2>&1 | Out-String
                if ($verOut -match "build\s*(\d+)") {
                    $buildNum = [int]$Matches[1]
                    $ver = "b$buildNum"
                    $sortKey = [Version]"$buildNum.0.0"
                } elseif ($verOut -match "version:\s*([0-9\.]+)") {
                    $ver = $Matches[1]
                    $sortKey = [Version]$ver
                }
            } catch {}
            $list += [pscustomobject]@{
                version = $ver
                key     = $sortKey
                path    = $cmd.Source
                name    = $cand
            }
            break
        }
    }

    # 3. LM Studio backends
    if (Test-Path $Backends) {
        Get-ChildItem $Backends -Directory -Filter "llama.cpp-$Platform-*" |
            ForEach-Object {
                $exe = Join-Path $_.FullName "llama-server.exe"
                if (-not (Test-Path $exe)) { return }
                $version = $_.Name -replace "^llama\.cpp-$Platform-", ""
                $list += [pscustomobject]@{
                    version = $version
                    key = [Version]$version
                    path    = $exe
                    name    = "llama-server.exe"
                }
            }
    }

    # 4. Local tools directory
    $toolsCandidates = @(
        (Join-Path $Root "tools\llama-server\llama-server.exe"),
        (Join-Path $Root "tools\llama-server\llama-server"),
        (Join-Path $Root "tools\llama.exe")
    )
    foreach ($tPath in $toolsCandidates) {
        if (Test-Path $tPath) {
            $list += [pscustomobject]@{
                version = "tools"
                key     = [Version]"0.0.1"
                path    = (Resolve-Path $tPath).Path
                name    = (Split-Path $tPath -Leaf)
            }
            break
        }
    }

    return $list
}

$installed = @(Get-Installed)
if (-not $installed) {
    throw "No llama engine found in PATH, $Backends, or tools/! Please install llama or llama.cpp."
}
$available = ($installed | Sort-Object key | ForEach-Object { $_.version }) -join ", "

function Get-RuntimeValue {
    # Reads one field out of runtime.json, or $null. It is read with Get-Content
    # rather than parsed by Python so this file stays the single resolver, and a
    # file it cannot read is treated as "not specified" -- which is the newest
    # build, the shipping default, not a silent pin.
    $file = Join-Path $Root "runtime.json"
    if (-not (Test-Path $file)) { return $null }
    try {
        $cfg = Get-Content $file -Raw | ConvertFrom-Json
    } catch {
        Write-Host "# runtime.json is unreadable ($($_.Exception.Message)); treating llama_version as unspecified"
        return $null
    }
    $value = $cfg.llama_version
    if ($value -is [string] -and $value.Trim()) { return $value.Trim() }
    return $null
}

$chosen = $null
$how = "newest installed"
$wanted = $null

if ($Version) {
    $wanted = $Version
    $how = "argument"
} elseif ($env:TRANSLATION_LLAMA_VERSION) {
    $wanted = $env:TRANSLATION_LLAMA_VERSION
    $how = "environment"
} else {
    $wanted = Get-RuntimeValue
    if ($wanted) { $how = "runtime.json llama_version" }
}

if ($wanted) {
    $chosen = $installed | Where-Object { $_.version -eq $wanted } | Select-Object -First 1
    if (-not $chosen) {
        # Loud, and listing what is there. The alternative -- serving the newest build
        # under the name of the one that was asked for -- is the one error that turns a
        # missing directory into an unexplainable difference between two runs.
        throw "llama.cpp $Platform-$wanted is not installed. Installed: $available"
    }
} else {
    $chosen = $installed | Sort-Object key | Select-Object -Last 1
}

Write-Host "# llama.cpp $Platform-$($chosen.version) ($how); installed: $available"
[pscustomobject]@{
    path      = $chosen.path
    version   = $chosen.version
    platform  = $Platform
    selected  = $how
    installed = $available
    is_cli    = ((Split-Path $chosen.path -Leaf) -match "^llama(\.exe)?$")
}