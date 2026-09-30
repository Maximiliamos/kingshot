@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_tugarin_bots.ps1"
exit /b %errorlevel%
