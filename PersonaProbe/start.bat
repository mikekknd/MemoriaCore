@echo off
chcp 65001 >nul 2>&1
title PersonaProbe Launcher
color 0B

echo ============================================
echo   PersonaProbe - One-Click Launcher
echo ============================================
echo.

:: Switch to script directory
cd /d "%~dp0"

:: Virtual environment priority: parent venv_ai_memory -> local venv -> system Python
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

:: Check key packages
"%PYTHON%" -c "import fastapi" >nul 2>&1
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

:: Port config
set API_PORT=8089
set STREAMLIT_PORT=8502

:: Check if API port is already in use
netstat -ano | findstr ":%API_PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [WARN] Port %API_PORT% is already in use. Skipping API server start.
    echo.
    goto :start_streamlit
)

:: Start FastAPI server
echo [1/2] Starting PersonaProbe API server on port %API_PORT% ...
start /B "" "%PYTHON%" server.py

:: Wait for API to be ready
echo      Waiting for API server to be ready ...
set RETRIES=0
:wait_loop
if %RETRIES% geq 20 (
    color 0E
    echo [WARN] API server did not respond within 20 seconds.
    echo        Continuing anyway -- Streamlit can still run standalone.
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

:: Start Streamlit
:start_streamlit
echo [2/2] Starting Streamlit UI on port %STREAMLIT_PORT% ...
start /B "" "%PYTHON%" -m streamlit run app.py --server.port %STREAMLIT_PORT% --server.headless true

:: Brief pause before opening browser
timeout /t 3 /nobreak >nul

:: Open browser
echo.
echo ============================================
echo   PersonaProbe started!
echo.
echo   Streamlit UI : http://localhost:%STREAMLIT_PORT%
echo   API server   : http://localhost:%API_PORT%
echo   API docs     : http://localhost:%API_PORT%/docs
echo ============================================
echo.
echo   To stop: simply close this window!
echo.
start "" "http://localhost:%STREAMLIT_PORT%"

echo Press any key to stop all services ...
pause >nul

echo Stopping services...
for /f "tokens=2 delims=," %%i in ('wmic process where "name='python.exe'" get ProcessId^,CommandLine /format:csv 2^>nul ^| findstr /i "server.py"') do taskkill /PID %%i /F >nul 2>&1
for /f "tokens=2 delims=," %%i in ('wmic process where "name='python.exe'" get ProcessId^,CommandLine /format:csv 2^>nul ^| findstr /i "streamlit"') do taskkill /PID %%i /F >nul 2>&1
