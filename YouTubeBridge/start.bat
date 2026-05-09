@echo off
chcp 65001 >nul 2>&1
title YouTubeBridge API Launcher
color 0B
setlocal

echo ============================================
echo   YouTubeBridge - API Launcher
echo ============================================
echo.

cd /d "%~dp0"

set API_PORT=8091
set "LOG_DIR=%~dp0..\runtime\log"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [INFO] Cleaning any current listener/process tree on port %API_PORT% before start...
call "%~dp0stop_8091.bat"
if errorlevel 1 (
    color 0C
    echo [ERROR] Port %API_PORT% is still occupied after cleanup.
    pause
    exit /b 1
)
echo [OK] Port %API_PORT% is free. Preparing YouTubeBridge API...
echo.

set PARENT_VENV=%~dp0..\venv_ai_memory\Scripts\python.exe
set LOCAL_VENV=%~dp0venv\Scripts\python.exe

if exist "%PARENT_VENV%" (
    echo [INFO] Using parent venv: venv_ai_memory
    set PYTHON=%PARENT_VENV%
) else if exist "%LOCAL_VENV%" (
    echo [INFO] Using local venv
    set PYTHON=%LOCAL_VENV%
) else (
    echo [WARN] No venv found, falling back to system Python.
    where python >nul 2>&1
    if %errorlevel% neq 0 (
        color 0C
        echo [ERROR] Python is not installed or not in PATH.
        pause
        exit /b 1
    )
    set PYTHON=python
)
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

"%PYTHON%" -c "import fastapi, pydantic, requests, uvicorn" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Installing dependencies from requirements.txt ...
    "%PYTHON%" -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        color 0C
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

:: Local dev default: if MemoriaCore has admin bypass enabled, let the bridge use it.
:: MemoriaCore still enforces its own admin_bypass_enabled and loopback checks.
if "%MEMORIACORE_ADMIN_BYPASS%"=="" set MEMORIACORE_ADMIN_BYPASS=1

echo.
echo ============================================
echo   Starting YouTubeBridge in foreground mode
echo.
echo   Control UI   : http://localhost:%API_PORT%/ui/
echo   Live page    : http://localhost:%API_PORT%/live/
echo   API server   : http://localhost:%API_PORT%
echo   API docs     : http://localhost:%API_PORT%/docs
echo ============================================
echo.
echo [INFO] Keep this window open while using the API. Close it or press Ctrl+C to stop.
echo [INFO] Console output is mirrored to: %LOG_DIR%\youtube_bridge_8091.foreground.log
echo [INFO] Open the Control UI after the server reports that it is running.

powershell -NoProfile -ExecutionPolicy Bypass -Command "& { $ProgressPreference='SilentlyContinue'; $env:MEMORIACORE_ADMIN_BYPASS='%MEMORIACORE_ADMIN_BYPASS%'; $env:PYTHONUTF8='%PYTHONUTF8%'; $env:PYTHONIOENCODING='%PYTHONIOENCODING%'; [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; $OutputEncoding = [Console]::OutputEncoding; $cmdLine = [char]34 + '%PYTHON%' + [char]34 + ' server.py 2>&1'; cmd /d /s /c $cmdLine | Tee-Object -FilePath '%LOG_DIR%\youtube_bridge_8091.foreground.log' -Append; $code = $LASTEXITCODE; exit $code }"
set EXIT_CODE=%errorlevel%

echo.
echo [INFO] YouTubeBridge API exited with code %EXIT_CODE%.
echo [INFO] Cleaning any remaining listener on port %API_PORT%...
call "%~dp0stop_8091.bat"
pause
exit /b %EXIT_CODE%
