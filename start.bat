@echo off
chcp 65001 >nul 2>&1
setlocal

cd /d "%~dp0"
set "API_PORT=8088"
set "LOG_DIR=%~dp0runtime\log"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "PYTHON=%~dp0venv_ai_memory\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo [INFO] Cleaning any current listener/process tree on port %API_PORT% before start...
call "%~dp0stop_%API_PORT%.bat"
if errorlevel 1 (
    echo [ERROR] Port %API_PORT% is still occupied after cleanup.
    pause
    exit /b 1
)
echo [OK] Port %API_PORT% is free. Starting MemoriaCore API in this foreground window...
echo [INFO] Keep this window open while using the API. Close it or press Ctrl+C to stop.
echo [INFO] URL: http://localhost:%API_PORT%

echo [INFO] Console output is mirrored to: %LOG_DIR%\api_8088.foreground.log
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { $ProgressPreference='SilentlyContinue'; $env:PYTHONUTF8='%PYTHONUTF8%'; $env:PYTHONIOENCODING='%PYTHONIOENCODING%'; [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; $OutputEncoding = [Console]::OutputEncoding; $cmdLine = [char]34 + '%PYTHON%' + [char]34 + ' run_server.py 2>&1'; cmd /d /s /c $cmdLine | Tee-Object -FilePath '%LOG_DIR%\api_8088.foreground.log' -Append; $code = $LASTEXITCODE; exit $code }"
set EXIT_CODE=%errorlevel%

echo.
echo [INFO] MemoriaCore API exited with code %EXIT_CODE%.
echo [INFO] Cleaning any remaining listener on port %API_PORT%...
call "%~dp0stop_%API_PORT%.bat"
pause
exit /b %EXIT_CODE%
