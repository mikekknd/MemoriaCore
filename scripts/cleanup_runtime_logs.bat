@echo off
chcp 65001 >nul 2>&1
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup_runtime_logs.ps1" %*
exit /b %errorlevel%
