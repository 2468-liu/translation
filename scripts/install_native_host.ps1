param(
    [string]$ExtensionId = "mgnbcjdhnfiiooeldkpbndgmffgapdll"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
# onedir, not onefile: see build_native_host.ps1 for why the unpack step is a
# silent dependency worth removing.
$HostExe = Join-Path $Root "scripts\native-host\translation-native-host\translation-native-host.exe"
if (-not (Test-Path $HostExe)) {
    throw "Native host not found. Run scripts\build_native_host.ps1 first."
}
if ($ExtensionId -notmatch "^[a-p]{32}$") {
    throw "ExtensionId must be Chrome's 32-character unpacked extension id."
}

$ManifestDir = Join-Path $env:LOCALAPPDATA "translation-native-host"
New-Item -ItemType Directory -Force -Path $ManifestDir | Out-Null
$ManifestPath = Join-Path $ManifestDir "translation_backend.json"
$Json = @{
    name = "translation_backend"
    description = "Local realtime subtitle backend"
    path = $HostExe
    type = "stdio"
    allowed_origins = @("chrome-extension://$ExtensionId/")
} | ConvertTo-Json

# Written without a byte order mark, deliberately.
#
# Set-Content -Encoding UTF8 is UTF8 *with* BOM on PowerShell 5.1, which is the
# shell this project runs in. The manifest that was installed carried EF BB BF,
# and strict UTF-8 JSON parsing rejects it outright. Whether Chrome's own parser
# tolerates a BOM is not something this repo can settle by reading, and a native
# messaging host that cannot be parsed produces no error the popup can show --
# connectNative simply fails and the start button does nothing. So the file is
# written the way JSON is defined, and the BOM cannot come back.
[System.IO.File]::WriteAllText($ManifestPath, $Json, (New-Object System.Text.UTF8Encoding($false)))

$Head = [System.IO.File]::ReadAllBytes($ManifestPath)[0..2]
if ($Head[0] -eq 0xEF -and $Head[1] -eq 0xBB -and $Head[2] -eq 0xBF) {
    throw "the manifest still starts with a UTF-8 BOM; refusing to register a host Chrome may not parse"
}

$RegistryPath = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\translation_backend"
New-Item -Force -Path $RegistryPath | Out-Null
Set-ItemProperty -Path $RegistryPath -Name "(default)" -Value $ManifestPath
Write-Host "Installed Native Messaging host for chrome-extension://$ExtensionId/"
Write-Host "  manifest: $ManifestPath (no BOM)"
Write-Host "  exe     : $HostExe"