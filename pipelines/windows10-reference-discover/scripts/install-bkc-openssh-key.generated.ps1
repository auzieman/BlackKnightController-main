$PublicKey = @'
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQDOdGhrUn8PmFdvy8MUvwojv1ajmqW47MvRqvt8YTYg/5LSal1YCTKoiiyXcICmvWR/DlSe2//tXMq706ncbg8lzZC8u9xGjznGySBPKgygyx2LT1ajRV48z42E+f84wJz9/8YFPEqOGdRfMkYT39pE/KLZNCQJCzs5Joxbeaf5i/Zg413PbeYdmI2gJ0lxWZQTkPG4XhzPz7VuSHQrIV/6F+HiyWmRkiDYv2wioqdz+ZF5LyaHWQ79a6JP/Tiap8jYUW4VhKBGtEiQqAlDL3pSaaO+SCZNYAoS17YCW775m9MVJnUGe9ToQpv7eEVUD1JuKwCy16GSLBOE0I46pqtROxxKQqMTwXHha+L7EsQLx5YaKJmNByIj4mK1xLDRoZScwR87cRZMqZOfPI4KAk0/vYoelIB6aFyDf3WNK2HOADzoJLYLZJPOKbtBumtNABYHrKo9xQyV1SgdGZ5UNNtN8BgvTEPU248fc8aq9BRJgCVRnEIhPZCYG2UQelfrMy6kef7c8YkO/Y6V5kQCUiQtNMpP+KPQWMVi1iwfnP/X1CD3StXLNKH57iS94Q9dp8lFk2+57fCYZez4Jkyzhy17Gc2T8LahcWSoTVED/6wUpAm/VLZ57j/kn4syAR0m6baDzr9e99KTOjuvds9ewPe1Z+2KKOf9iV6QI5oTP1tG1Q== root@ns1.lab.auzietek.com
'@
$UserName = 'depadmin'

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
