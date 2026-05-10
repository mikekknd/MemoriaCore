@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set PYTHON=venv_ai_memory\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python
set "LOG_DIR=%~dp0runtime\log"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [INFO] Cleaning existing MemoriaCore process tree on port 8088...
call "%~dp0stop_8088.bat"
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] Port 8088 is still occupied after cleanup.
    pause
    exit /b 1
)

echo [INFO] 8088 hot reload stdout: %LOG_DIR%\api_8088_hot_reload.out.log
echo [INFO] 8088 hot reload stderr: %LOG_DIR%\api_8088_hot_reload.err.log
"%PYTHON%" run_server_hot_reload.py 1>>"%LOG_DIR%\api_8088_hot_reload.out.log" 2>>"%LOG_DIR%\api_8088_hot_reload.err.log"

pause
