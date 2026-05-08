@echo off
cd /d "%~dp0"
set "LOG_DIR=%~dp0runtime\log"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [INFO] Cleaning existing MemoriaCore process tree on port 8088...
call "%~dp0stop_8088.bat"
if %errorlevel% neq 0 (
    echo [ERROR] Port 8088 is still occupied after cleanup.
    pause
    exit /b 1
)

echo [INFO] 8088 stdout: %LOG_DIR%\api_8088.out.log
echo [INFO] 8088 stderr: %LOG_DIR%\api_8088.err.log
venv_ai_memory\Scripts\python.exe run_server.py 1>>"%LOG_DIR%\api_8088.out.log" 2>>"%LOG_DIR%\api_8088.err.log"
