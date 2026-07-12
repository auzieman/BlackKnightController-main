param(
    [string]$ArtifactRoot = "C:\BKC\WinPE",
    [string]$WorkDir = "C:\BKC\WinPE\amd64",
    [string]$OutputDir = "C:\BKC\WinPE\out",
    [string]$ArtifactName = "bkc-winpe-amd64"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $ArtifactRoot, $OutputDir | Out-Null

$copypeCandidates = @(
    "C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit\Windows Preinstallation Environment\copype.cmd",
    "C:\Program Files (x86)\Windows Kits\10\Windows Preinstallation Environment\copype.cmd"
)
$copype = $copypeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $copype) {
    throw "copype.cmd was not found. Install Windows ADK and WinPE add-on first."
}

if (Test-Path $WorkDir) {
    Remove-Item -Recurse -Force $WorkDir
}

cmd.exe /c "`"$copype`" amd64 `"$WorkDir`""

$mediaScripts = Join-Path $WorkDir "media\BKC"
New-Item -ItemType Directory -Force $mediaScripts | Out-Null

$startnet = Join-Path $WorkDir "mount\Windows\System32\startnet.cmd"
Write-Output "BKC WinPE workdir prepared at $WorkDir"
Write-Output "Next phase will mount boot.wim, add BKC startnet.cmd, optional components, and drivers."

New-Item -ItemType Directory -Force (Join-Path $OutputDir $ArtifactName) | Out-Null
Copy-Item -Recurse -Force (Join-Path $WorkDir "media\*") (Join-Path $OutputDir $ArtifactName)
Get-ChildItem -Recurse (Join-Path $OutputDir $ArtifactName) | Select-Object FullName, Length
