@echo off
chcp 65001 >nul 2>&1
title LLM Memory System - Shutdown
color 0E

echo ============================================
echo   Stopping all LLM Memory services ...
echo ============================================
echo.

:: -- Kill all uvicorn processes --
echo   Stopping FastAPI (uvicorn) ...
taskkill /F /IM uvicorn.exe >nul 2>&1

:: -- Kill python processes running uvicorn or streamlit --
for /f "tokens=2 delims=," %%i in ('wmic process where "name='python.exe'" get ProcessId^,CommandLine /format:csv 2^>nul ^| findstr /i "uvicorn"') do (
    echo   Killing python uvicorn PID: %%i
    taskkill /PID %%i /F >nul 2>&1
)

for /f "tokens=2 delims=," %%i in ('wmic process where "name='python.exe'" get ProcessId^,CommandLine /format:csv 2^>nul ^| findstr /i "streamlit"') do (
    echo   Killing python streamlit PID: %%i
    taskkill /PID %%i /F >nul 2>&1
)

echo.
echo   All services stopped.
echo.
pause
