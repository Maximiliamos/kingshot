@echo off
cd /d "%~dp0"
wscript.exe //B "%~dp0run_gui.vbs"
exit /b
