@echo off
setlocal EnableExtensions

rem Clean pytest temp folders that can be left with broken Windows ACLs.
rem Run this from anywhere. The script resolves the repo root from its own path.

net session >nul 2>nul
if %errorlevel% neq 0 (
  echo [INFO] Requesting Administrator permission...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

pushd "%~dp0\.." >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Cannot enter repository root from "%~dp0\..".
  exit /b 1
)

echo [INFO] Repository: %CD%
echo [INFO] This will remove pytest temp folders only.
echo.

call :clean ".pyTestTemp"

for /d %%D in ("tests\.basetemp-*" "tests\.tmp-*" "tests\tmpcoreinsights") do (
  call :clean "%%~D"
)

echo.
echo [DONE] Cleanup attempt finished.
popd >nul 2>nul
exit /b 0

:clean
set "TARGET=%~1"
if "%TARGET%"=="" exit /b 0
if not exist "%TARGET%" (
  echo [SKIP] %TARGET% does not exist.
  exit /b 0
)

echo [CLEAN] %TARGET%

takeown /F "%TARGET%" /R /D Y >nul 2>nul
icacls "%TARGET%" /grant "%USERNAME%":F /T /C >nul 2>nul
attrib -R -S -H "%TARGET%\*" /S /D >nul 2>nul
rmdir /S /Q "%TARGET%" >nul 2>nul

if exist "%TARGET%" (
  echo [WARN] Could not remove %TARGET%. Close processes using it or run this bat as Administrator.
) else (
  echo [OK] Removed %TARGET%.
)
exit /b 0
