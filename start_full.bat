@echo off
chcp 65001 >nul 2>&1
title LLM Memory System Launcher
color 0A

echo ============================================
echo   LLM Memory System - One-Click Launcher
echo ============================================
echo.

:: -- Switch to script directory --
cd /d "%~dp0"

:: -- Virtual environment path (relative to this bat file) --
set VENV_PYTHON=%~dp0venv_ai_memory\Scripts\python.exe
set VENV_ACTIVATE=%~dp0venv_ai_memory\Scripts\activate.bat

:: -- Check virtual environment --
if exist "%VENV_PYTHON%" (
    echo [INFO] Using virtual environment: venv_ai_memory
    set PYTHON=%VENV_PYTHON%
) else (
    echo [WARN] venv_ai_memory not found, falling back to system Python.
    where python >nul 2>&1
    if %errorlevel% neq 0 (
        color 0C
        echo [ERROR] Python is not installed or not in PATH.
        pause
        exit /b 1
    )
    set PYTHON=python
)

:: -- Check key packages --
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

:: -- Port config (edit here if needed) --
set API_PORT=8088
set STREAMLIT_PORT=8501

:: -- Check if API port is occupied --
netstat -ano | findstr ":%API_PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [WARN] Port %API_PORT% is already in use.
    echo        If FastAPI is already running, Streamlit will connect to it.
    echo.
    goto :start_streamlit
)

:: -- Start FastAPI backend --
echo [1/2] Starting FastAPI backend on port %API_PORT% ...
start /B "" "%VENV_PYTHON%" -m uvicorn api.main:app --host 0.0.0.0 --port %API_PORT%

:: -- Wait for FastAPI to be ready --
echo      Waiting for backend to be ready ...
set RETRIES=0
:wait_loop
if %RETRIES% geq 30 (
    color 0C
    echo [ERROR] FastAPI backend failed to start within 30 seconds.
    echo         Check the FastAPI window for error messages.
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
"%PYTHON%" -c "import requests; r=requests.get('http://localhost:%API_PORT%/api/v1/health',timeout=2); exit(0 if r.ok else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    set /a RETRIES+=1
    goto :wait_loop
)
echo      Backend is ready!
echo.

:: -- Start Streamlit frontend --
:start_streamlit
echo [2/2] Starting Streamlit dashboard on port %STREAMLIT_PORT% ...
start /B "" "%VENV_PYTHON%" -m streamlit run app.py --server.port %STREAMLIT_PORT% --server.headless true

:: -- Wait for Streamlit --
timeout /t 3 /nobreak >nul

:: -- Open browser --
echo.
echo ============================================
echo   All services started successfully!
echo.
echo   FastAPI  : http://localhost:%API_PORT%
echo   API Docs : http://localhost:%API_PORT%/docs
echo   Streamlit: http://localhost:%STREAMLIT_PORT%
echo ============================================
echo.
echo   To stop: simply close this window!
echo            (Logs from both services will appear here)
echo.
start "" "http://localhost:%STREAMLIT_PORT%"

echo Press any key to stop all services and close this launcher ...
pause >nul

echo Stopping services...
taskkill /F /IM uvicorn.exe >nul 2>&1
for /f "tokens=2 delims=," %%i in ('wmic process where "name='python.exe'" get ProcessId^,CommandLine /format:csv 2^>nul ^| findstr /i "uvicorn"') do taskkill /PID %%i /F >nul 2>&1
for /f "tokens=2 delims=," %%i in ('wmic process where "name='python.exe'" get ProcessId^,CommandLine /format:csv 2^>nul ^| findstr /i "streamlit"') do taskkill /PID %%i /F >nul 2>&1
