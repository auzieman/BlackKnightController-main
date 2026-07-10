@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%WINDIR%\Setup\Scripts\bkc-firstboot.ps1" > "%WINDIR%\Setup\Scripts\bkc-firstboot.out.log" 2>&1
exit /b 0
