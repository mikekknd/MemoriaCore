@echo off
chcp 65001 >nul 2>&1
title YouTubeBridge Launcher
color 0B

echo ============================================
echo   YouTubeBridge - One-Click Launcher
echo ============================================
echo.

cd /d "%~dp0"

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

"%PYTHON%" -c "import fastapi, streamlit, requests" >nul 2>&1
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

set API_PORT=8091
set STREAMLIT_PORT=8503
set API_STARTED=0
set STREAMLIT_STARTED=0

:: Local dev default: if MemoriaCore has admin bypass enabled, let the bridge use it.
:: MemoriaCore still enforces its own admin_bypass_enabled and loopback checks.
if "%MEMORIACORE_ADMIN_BYPASS%"=="" set MEMORIACORE_ADMIN_BYPASS=1

netstat -ano | findstr ":%API_PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [WARN] Port %API_PORT% is already in use. Skipping API server start.
    goto :start_streamlit
)

echo [1/2] Starting YouTubeBridge API server on port %API_PORT% ...
start /B "" "%PYTHON%" server.py
set API_STARTED=1

echo      Waiting for API server to be ready ...
set RETRIES=0
:wait_loop
if %RETRIES% geq 20 (
    color 0E
    echo [WARN] API server did not respond within 20 seconds.
    goto :start_streamlit
)
timeout /t 1 /nobreak >nul
"%PYTHON%" -c "import requests; r=requests.get('http://localhost:%API_PORT%/health',timeout=2); exit(0 if r.ok else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    set /a RETRIES+=1
    goto :wait_loop
)
echo      API server is ready!
echo.

:start_streamlit
netstat -ano | findstr ":%STREAMLIT_PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [WARN] Port %STREAMLIT_PORT% is already in use. Skipping Streamlit UI start.
    goto :after_streamlit
)

echo [2/2] Starting Streamlit UI on port %STREAMLIT_PORT% ...
start /B "" "%PYTHON%" -m streamlit run app.py --server.port %STREAMLIT_PORT% --server.headless true
set STREAMLIT_STARTED=1

timeout /t 3 /nobreak >nul

:after_streamlit

echo.
echo ============================================
echo   YouTubeBridge started!
echo.
echo   Streamlit UI : http://localhost:%STREAMLIT_PORT%
echo   API server   : http://localhost:%API_PORT%
echo   API docs     : http://localhost:%API_PORT%/docs
echo ============================================
echo.
start "" "http://localhost:%STREAMLIT_PORT%"

echo Press any key to stop all services ...
pause >nul

echo Stopping services...
if "%STREAMLIT_STARTED%"=="1" call :stop_port %STREAMLIT_PORT%
if "%API_STARTED%"=="1" call :stop_port %API_PORT%
exit /b 0

:stop_port
set TARGET_PORT=%~1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%TARGET_PORT% " ^| findstr "LISTENING"') do (
    echo      Stopping PID %%p on port %TARGET_PORT%
    taskkill /PID %%p /F >nul 2>&1
)
exit /b 0
