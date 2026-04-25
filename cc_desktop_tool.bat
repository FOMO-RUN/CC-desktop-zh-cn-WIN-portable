@echo off
chcp 65001 >nul 2>&1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0cc_desktop_tool.ps1"
exit /b %ERRORLEVEL%
