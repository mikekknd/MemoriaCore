@echo off
chcp 65001 >nul 2>&1
setlocal

set API_PORT=8091
set "BRIDGE_ROOT=%~dp0."

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_8091.ps1" -Port %API_PORT% -BridgeRoot "%BRIDGE_ROOT%"
exit /b %errorlevel%
