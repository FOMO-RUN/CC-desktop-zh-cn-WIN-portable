@echo off
chcp 65001 >nul 2>&1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0cc_desktop_tool_zh.ps1"
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo 工具启动失败，错误码: %ERR%
  pause
)
exit /b %ERR%
