# Shared probes for the sweep/replay scripts.
#
# Why this file exists: the probes these scripts used are built on WMI/CIM, and
# WMI is refused outright in a confined session -- `Get-CimInstance : 拒绝访问`,
# HRESULT 0x80041003, and `Get-NetTCPConnection` is CIM underneath so it fails the
# same way. That produced two distinct failure modes, both worse than an error:
#
#   * `Get-NetTCPConnection ... -ErrorAction SilentlyContinue` returned nothing,
#     so a server that was up and serving looked like a server that was not.
#     replay.ps1 aborted with "server did not come up on 8765" while the process
#     was listening.
#   * `Get-CimInstance Win32_Process` (also silenced) returned nothing, so the
#     "did the draft model actually load" assertion compared $null against a
#     pattern and reported draft=False. A check that cannot run was reading as a
#     check that passed -- the failure this repository has recorded more than once.
#
# So: ports are probed with a socket, process command lines report "unknown"
# rather than an empty string, and callers must treat unknown as unknown.

function Test-TcpPort {
    <#
    .SYNOPSIS
        Is something listening on 127.0.0.1:$Port?
    .DESCRIPTION
        A socket connect, not Get-NetTCPConnection, so it works without WMI.
    #>
    param([int] $Port, [int] $TimeoutMs = 400)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $task.Wait($TimeoutMs)) { return $false }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-ProcessCommandLine {
    <#
    .SYNOPSIS
        Command line of a process by name, or $null when it cannot be read.
    .DESCRIPTION
        Returns a string when it can be read and $null when it cannot. The caller
        must decide what "unknown" means for its own assertion; silently treating
        unknown as "no flags present" is what made the draft check vacuous.
    #>
    param([string] $Name, [int] $ProcessId = 0)
    $proc = if ($ProcessId) {
        Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    } else {
        Get-Process -Name $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if (-not $proc) { return $null }
    # PowerShell 7 exposes CommandLine on the returned object; 5.1 does not.
    $property = $proc.PSObject.Properties["CommandLine"]
    if ($property -and $property.Value) { return [string] $property.Value }
    try {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.Id)" -ErrorAction Stop
        if ($cim -and $cim.CommandLine) { return [string] $cim.CommandLine }
    } catch {
        # WMI refused. Report unknown rather than an empty string.
        return $null
    }
    return $null
}

function Get-ProcessPath {
    <#
    .SYNOPSIS
        Executable path of a process by name, or $null.
    .DESCRIPTION
        Used to name who is still holding the GPU. Plain .NET, no WMI.
    #>
    param([string] $Name)
    $proc = Get-Process -Name $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $proc) { return $null }
    try { return $proc.Path } catch { return $null }
}

function Get-HyMt2Gguf {
    <#
    .SYNOPSIS
        The one Hy-MT2 Q4_K_M weight file, or $null. Throws if the choice is ambiguous.
    .DESCRIPTION
        models\hy-mt2-1.8b currently holds two files matching *Q4_K_M*:
        Hy-MT2-1.8B-Q4_K_M.gguf, which every number in REPORT.md was measured on, and
        Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf, added 10-08 and never tested.

        Every caller used to write `Get-ChildItem *Q4_K_M*.gguf | Select-Object -First 1`,
        which is a coin flip between them. smoke.ps1 had already been bitten by the same
        shape -- the directory once held Hy-MT2-1.8B-1.25bit-v2.gguf, which does not even
        load ("tensor 'blk.0.attn_k_norm.weight' has offset ... expected"), so the wrong
        pick surfaced three steps later as "the translation backend is not on 8080". That
        note recorded the conclusion and the guard went into smoke.ps1 alone, leaving
        start.ps1 and replay.ps1 to make the same choice unguarded.

        An ambiguous set of weights is the same failure class as a mismatched one, so
        this returns nothing rather than picking, and says which files collided. The
        caller names the file it wanted; this does not guess which one was right,
        because only the run history knows that.
    #>
    param(
        [string] $Dir = "",
        [switch] $BaseName
    )
    if (-not $Dir) {
        $candidates = @(
            "D:\TranslationDesktop\models\hy-mt2-1.8b",
            (Join-Path (Split-Path -Parent $PSScriptRoot) "models\hy-mt2-1.8b"),
            "D:\TranslationDesktop\models",
            (Join-Path (Split-Path -Parent $PSScriptRoot) "models")
        )
        foreach ($c in $candidates) {
            if (Test-Path $c) {
                $check = Get-ChildItem (Join-Path $c "*Q4_K_M*.gguf") -ErrorAction SilentlyContinue
                if ($check) {
                    $Dir = $c
                    break
                }
            }
        }
        if (-not $Dir) {
            $Dir = Join-Path (Split-Path -Parent $PSScriptRoot) "models\hy-mt2-1.8b"
        }
    }

    # An explicit choice beats the pattern, so the guard has a way through. Without
    # one, putting a second weight in the directory wedges every script that starts a
    # backend, and the way people respond to a guard like that is to delete the guard.
    # An exact file name, not another glob: a glob would reintroduce the coin flip one
    # level up, where nothing can catch it.
    if ($env:TRANSLATION_HY_GGUF) {
        $named = Join-Path $Dir $env:TRANSLATION_HY_GGUF
        if (-not (Test-Path $named)) {
            throw "TRANSLATION_HY_GGUF=$env:TRANSLATION_HY_GGUF 不在 $Dir 里。"
        }
        $item = Get-Item $named
        if ($BaseName) { return $item.BaseName }
        return $item
    }

    $found = @(Get-ChildItem (Join-Path $Dir "*Q4_K_M*.gguf") -ErrorAction SilentlyContinue)
    if ($found.Count -gt 1) {
        throw @"
models\hy-mt2-1.8b 里匹配 *Q4_K_M* 的权重有 $($found.Count) 个：$($found.Name -join ' / ')
  请显式指定用哪一个：设 TRANSLATION_HY_GGUF=<完整文件名>，或只留一个在目录里。
  历史上所有数字都是 Hy-MT2-1.8B-Q4_K_M.gguf 测的；StreamRevise-v4 未测过，
  不能靠文件名猜，也不能靠目录顺序。
"@
    }
    if ($found.Count -eq 0) { return $null }
    if ($BaseName) { return $found[0].BaseName }
    return $found[0]
}

function Get-IndexTranslateGguf {
    <#
    .SYNOPSIS
        The Index-Translate-2B Q4_K_M weight file, or $null.
    #>
    param(
        [string] $Dir = "",
        [switch] $BaseName
    )
    if (-not $Dir) {
        $candidates = @(
            "D:\TranslationDesktop\models\Index-Translate",
            (Join-Path (Split-Path -Parent $PSScriptRoot) "models\Index-Translate"),
            "D:\TranslationDesktop\models",
            (Join-Path (Split-Path -Parent $PSScriptRoot) "models")
        )
        foreach ($c in $candidates) {
            if (Test-Path $c) {
                if (Test-Path (Join-Path $c "Index-Translate-2B.Q4_K_M.gguf")) {
                    $Dir = $c
                    break
                }
            }
        }
    }
    if (-not $Dir -or -not (Test-Path $Dir)) { return $null }

    if ($env:TRANSLATION_INDEX_GGUF) {
        $named = Join-Path $Dir $env:TRANSLATION_INDEX_GGUF
        if (Test-Path $named) {
            $item = Get-Item $named
            if ($BaseName) { return $item.BaseName }
            return $item
        }
    }

    $q4 = Get-ChildItem (Join-Path $Dir "*Index-Translate*Q4_K_M*.gguf") -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $q4) {
        $q4 = Get-ChildItem (Join-Path $Dir "*Q4_K_M*.gguf") -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if (-not $q4) {
        $q4 = Get-ChildItem (Join-Path $Dir "*Index-Translate*.gguf") -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if ($q4) {
        if ($BaseName) { return $q4.BaseName }
        return $q4
    }
    return $null
}
