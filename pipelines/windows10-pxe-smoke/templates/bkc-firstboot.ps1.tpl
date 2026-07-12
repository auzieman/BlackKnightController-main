$ErrorActionPreference = "Stop"
$logPath = "C:\ProgramData\BKC\firstboot.log"
New-Item -ItemType Directory -Force (Split-Path $logPath) | Out-Null
Start-Transcript -Path $logPath -Append | Out-Null

$PublicKey = @'
${dictionary.target_ssh_authorized_key}
'@
$UserName = "${dictionary.target_admin_user}"

if (-not $PublicKey.StartsWith("ssh-")) {
    throw "PublicKey does not look like an OpenSSH public key."
}

$sshService = Get-Service -Name sshd -ErrorAction SilentlyContinue
if (-not $sshService) {
    $capability = Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
    if ($capability.State -ne "Installed") {
        Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
    }
}

Set-Service -Name sshd -StartupType Automatic
Start-Service -Name sshd

if (Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue) {
    if (-not (Get-NetFirewallRule -Name "BKC-OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name "BKC-OpenSSH-Server-In-TCP" -DisplayName "BKC OpenSSH Server (sshd)" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    }
}

$programDataSsh = "C:\ProgramData\ssh"
New-Item -ItemType Directory -Force $programDataSsh | Out-Null
$adminKeys = Join-Path $programDataSsh "administrators_authorized_keys"
Set-Content -Path $adminKeys -Value $PublicKey -Encoding ascii
icacls $adminKeys /inheritance:r | Out-Null
icacls $adminKeys /remove:g "Users" "Authenticated Users" "Everyone" 2>$null | Out-Null
icacls $adminKeys /grant:r "Administrators:F" "SYSTEM:F" | Out-Null

$user = Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue
if ($user) {
    $userSsh = Join-Path (Join-Path "C:\Users" $UserName) ".ssh"
    $userKeys = Join-Path $userSsh "authorized_keys"
    New-Item -ItemType Directory -Force $userSsh | Out-Null
    Set-Content -Path $userKeys -Value $PublicKey -Encoding ascii
    $userSid = (New-Object System.Security.Principal.NTAccount($UserName)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    icacls $userSsh /inheritance:r | Out-Null
    icacls $userSsh /grant:r "*$($userSid):F" "Administrators:F" "SYSTEM:F" | Out-Null
    icacls $userKeys /inheritance:r | Out-Null
    icacls $userKeys /grant:r "*$($userSid):F" "Administrators:F" "SYSTEM:F" | Out-Null
}

Restart-Service -Name sshd

if ("${dictionary.enable_chocolatey}" -eq "true") {
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    iex ((New-Object System.Net.WebClient).DownloadString("https://community.chocolatey.org/install.ps1"))
}

if ("${dictionary.enable_rustdesk}" -eq "true" -and (Get-Command choco.exe -ErrorAction SilentlyContinue)) {
    choco install rustdesk -y --no-progress
}

Write-Output "bkc-windows-firstboot-complete user=$UserName"
Stop-Transcript | Out-Null
