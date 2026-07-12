param(
    [Parameter(Mandatory = $true)]
    [string]$PublicKey,

    [string]$UserName = "depadmin"
)

$ErrorActionPreference = "Stop"
$logPath = "C:\ProgramData\ssh\bkc-openssh-bootstrap.log"
Start-Transcript -Path $logPath -Append | Out-Null

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
    $rule = Get-NetFirewallRule -Name "BKC-OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
    if (-not $rule) {
        New-NetFirewallRule `
            -Name "BKC-OpenSSH-Server-In-TCP" `
            -DisplayName "BKC OpenSSH Server (sshd)" `
            -Enabled True `
            -Direction Inbound `
            -Protocol TCP `
            -Action Allow `
            -LocalPort 22 | Out-Null
    }
}

$programDataSsh = "C:\ProgramData\ssh"
New-Item -ItemType Directory -Force $programDataSsh | Out-Null
$adminKeys = Join-Path $programDataSsh "administrators_authorized_keys"
Set-Content -Path $adminKeys -Value $PublicKey -Encoding ascii

$acl = Get-Acl $adminKeys
$acl.SetOwner([System.Security.Principal.NTAccount]"Administrators")
Set-Acl -Path $adminKeys -AclObject $acl
icacls $adminKeys /inheritance:r | Out-Null
icacls $adminKeys /remove:g "Users" "Authenticated Users" "Everyone" 2>$null | Out-Null
icacls $adminKeys /grant:r "Administrators:F" "SYSTEM:F" | Out-Null

$user = Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue
if ($user) {
    $userProfile = Join-Path "C:\Users" $UserName
    $userSsh = Join-Path $userProfile ".ssh"
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

Get-Item $adminKeys | Format-List FullName,Length
icacls $adminKeys
Get-Content "C:\ProgramData\ssh\sshd_config" -ErrorAction SilentlyContinue | Select-String -Pattern "AuthorizedKeysFile|administrators_authorized_keys|PubkeyAuthentication|Match Group" -Context 0,2

Write-Output "bkc-openssh-key-installed user=$UserName admin_keys=$adminKeys"
Stop-Transcript | Out-Null
