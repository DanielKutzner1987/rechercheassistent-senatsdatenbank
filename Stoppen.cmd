@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" -Action Stop
if errorlevel 1 echo Stoppen fehlgeschlagen. Bitte die Fehlermeldung oben beachten.
pause
