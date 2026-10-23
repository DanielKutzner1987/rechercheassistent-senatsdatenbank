@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" -Action Import
if errorlevel 1 echo Import fehlgeschlagen. Bitte die Fehlermeldung oben beachten.
pause
