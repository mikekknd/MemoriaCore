@echo off
cd /d "%~dp0"
set "LOG_DIR=%~dp0runtime\log"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
:: 清掉佔用 8088 埠的舊進程（PowerShell 隔離，避免 taskkill 把 Ctrl+C 傳回 batch）
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8088 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo [INFO] 8088 stdout: %LOG_DIR%\api_8088.out.log
echo [INFO] 8088 stderr: %LOG_DIR%\api_8088.err.log
venv_ai_memory\Scripts\python.exe run_server.py 1>>"%LOG_DIR%\api_8088.out.log" 2>>"%LOG_DIR%\api_8088.err.log"
