param(
    [string]$OutputDir = "C:\BKC\WinPE\out\bkc-winpe-amd64",
    [string]$TargetHost = "192.168.1.10",
    [string]$TargetPath = "/srv/netboot/windows/bkc-winpe/amd64"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $OutputDir)) {
    throw "WinPE output directory does not exist: $OutputDir"
}

Write-Output "Publish contract:"
Write-Output "  source=$OutputDir"
Write-Output "  target=root@$TargetHost:$TargetPath"
Write-Output "Next phase will use BKC-approved copy transport instead of ad hoc manual copy."
