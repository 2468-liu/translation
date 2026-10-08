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
