@echo off
setlocal EnableExtensions
set LOG=X:\bkc-winpe-setup.log
echo bkc-winpe-setup-start > %LOG%
wpeinit >> %LOG% 2>&1
ipconfig /all >> %LOG%

set MEDIA=Z:
set MEDIA_UNC=\\${dictionary.pxe_http_host}\${dictionary.windows_media_share}
set ANSWER=%SystemRoot%\System32\Autounattend.xml

echo Mapping %MEDIA_UNC% >> %LOG%
net use %MEDIA% %MEDIA_UNC% /user:guest "" >> %LOG% 2>&1
if errorlevel 1 (
  net use %MEDIA% %MEDIA_UNC% >> %LOG% 2>&1
)

if not exist %MEDIA%\setup.exe (
  echo setup.exe missing from %MEDIA_UNC% >> %LOG%
  type %LOG%
  pause
  exit /b 20
)

if not exist %ANSWER% (
  echo answer file missing from %ANSWER% >> %LOG%
  type %LOG%
  pause
  exit /b 21
)

echo Launching Windows setup >> %LOG%
%MEDIA%\setup.exe /unattend:%ANSWER% /noreboot >> %LOG% 2>&1
echo setup returned %ERRORLEVEL% >> %LOG%
type %LOG%
exit /b %ERRORLEVEL%
