@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ============================================
echo   Stop MemoriaCore / YouTubeBridge servers
echo ============================================
echo.

call "%~dp0stop_8088.bat"
set STOP_8088_RC=%errorlevel%

call "%~dp0YouTubeBridge\stop_8091.bat"
set STOP_8091_RC=%errorlevel%

if not "%STOP_8088_RC%"=="0" exit /b %STOP_8088_RC%
if not "%STOP_8091_RC%"=="0" exit /b %STOP_8091_RC%
exit /b 0
