@echo off
chcp 65001 >nul 2>&1
set API_PORT=8088
set "REPO_ROOT=%~dp0."

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_8088.ps1" -Port %API_PORT% -RepoRoot "%REPO_ROOT%"
exit /b %errorlevel%
