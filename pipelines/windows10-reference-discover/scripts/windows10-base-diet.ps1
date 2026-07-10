$ErrorActionPreference = "Stop"

Write-Output "bkc-windows10-diet-start"

powercfg /hibernate off
powercfg /setactive SCHEME_MIN | Out-Null

$contentDelivery = "HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
if (Test-Path $contentDelivery) {
    Set-ItemProperty $contentDelivery -Name "SilentInstalledAppsEnabled" -Value 0 -ErrorAction SilentlyContinue
    Set-ItemProperty $contentDelivery -Name "SystemPaneSuggestionsEnabled" -Value 0 -ErrorAction SilentlyContinue
    Set-ItemProperty $contentDelivery -Name "SubscribedContent-338388Enabled" -Value 0 -ErrorAction SilentlyContinue
    Set-ItemProperty $contentDelivery -Name "SubscribedContent-338389Enabled" -Value 0 -ErrorAction SilentlyContinue
}

$servicesToDisable = @(
    "DiagTrack",
    "MapsBroker",
    "WSearch",
    "XblAuthManager",
    "XblGameSave",
    "XboxGipSvc",
    "XboxNetApiSvc"
)

foreach ($name in $servicesToDisable) {
    $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($svc) {
        Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
        Set-Service -Name $name -StartupType Disabled -ErrorAction SilentlyContinue
    }
}

Get-ScheduledTask -TaskPath "\Microsoft\Windows\Application Experience\" -ErrorAction SilentlyContinue |
    Disable-ScheduledTask -ErrorAction SilentlyContinue | Out-Null

Get-ScheduledTask -TaskPath "\Microsoft\Windows\Customer Experience Improvement Program\" -ErrorAction SilentlyContinue |
    Disable-ScheduledTask -ErrorAction SilentlyContinue | Out-Null

$provisionedPatterns = @(
    "*Xbox*",
    "*BingWeather*",
    "*GetHelp*",
    "*Getstarted*",
    "*MicrosoftOfficeHub*",
    "*MicrosoftSolitaireCollection*",
    "*People*",
    "*SkypeApp*",
    "*ZuneMusic*",
    "*ZuneVideo*"
)

foreach ($pattern in $provisionedPatterns) {
    Get-AppxProvisionedPackage -Online |
        Where-Object { $_.DisplayName -like $pattern } |
        Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue | Out-Null
}

Write-Output "bkc-windows10-diet-complete"
