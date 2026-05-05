@echo off
chcp 65001 >nul 2>&1
title YouTubeBridge Hot Reload API
color 0B

cd /d "%~dp0"

set API_PORT=8091
set PARENT_VENV=%~dp0..\venv_ai_memory\Scripts\python.exe
set LOCAL_VENV=%~dp0venv\Scripts\python.exe

if exist "%PARENT_VENV%" (
    set PYTHON=%PARENT_VENV%
) else if exist "%LOCAL_VENV%" (
    set PYTHON=%LOCAL_VENV%
) else (
    set PYTHON=python
)

echo ============================================
echo   YouTubeBridge API hot reload
echo   URL: http://127.0.0.1:%API_PORT%/live/
echo ============================================
echo.

echo [INFO] Cleaning existing YouTubeBridge process tree on port %API_PORT%...
call "%~dp0stop_8091.bat"
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] Port %API_PORT% is still occupied after cleanup.
    pause
    exit /b 1
)

echo [INFO] Starting uvicorn reload server...
echo [INFO] Python: %PYTHON%
echo.

"%PYTHON%" run_server_hot_reload.py

pause
