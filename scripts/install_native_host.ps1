param(
    [Parameter(Mandatory = $true)]
    [string]$ExtensionId
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$HostExe = Join-Path $Root "scripts\native-host\translation-native-host.exe"
if (-not (Test-Path $HostExe)) {
    throw "Native host not found. Run scripts\build_native_host.ps1 first."
}
if ($ExtensionId -notmatch "^[a-p]{32}$") {
    throw "ExtensionId must be Chrome's 32-character unpacked extension id."
}

$ManifestDir = Join-Path $env:LOCALAPPDATA "translation-native-host"
New-Item -ItemType Directory -Force -Path $ManifestDir | Out-Null
$ManifestPath = Join-Path $ManifestDir "translation_backend.json"
@{
    name = "translation_backend"
    description = "Local realtime subtitle backend"
    path = $HostExe
    type = "stdio"
    allowed_origins = @("chrome-extension://$ExtensionId/")
} | ConvertTo-Json | Set-Content -Encoding UTF8 $ManifestPath

$RegistryPath = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\translation_backend"
New-Item -Force -Path $RegistryPath | Out-Null
Set-ItemProperty -Path $RegistryPath -Name "(default)" -Value $ManifestPath
Write-Host "Installed Native Messaging host for chrome-extension://$ExtensionId/"
