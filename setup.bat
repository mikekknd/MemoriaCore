@echo off
echo ===================================================
echo [System] AI Memory Environment Setup (Python 3.12)
echo ===================================================

echo [Info] Checking for Python 3.12 via Windows Launcher...
py -3.12 --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [Error] Python 3.12 is not found on this system!
    echo Please download and run the standard Python 3.12 Windows installer.
    pause
    exit /b
)
echo [Success] Python 3.12 detected.

set VENV_DIR=venv_ai_memory
if exist %VENV_DIR% (
    echo [Info] Removing old virtual environment [%VENV_DIR%]...
    rmdir /s /q %VENV_DIR%
)

echo [Info] Creating new virtual environment using Python 3.12...
py -3.12 -m venv %VENV_DIR%

echo [Info] Activating virtual environment...
call %VENV_DIR%\Scripts\activate

echo [Info] Upgrading pip...
python -m pip install --upgrade pip

echo [Info] Installing dependencies from requirements.txt...
pip install -r requirements.txt

echo ===================================================
echo [Success] Setup complete! 
echo [Action] To run your app, keep this terminal open and type:
echo streamlit run app.py
echo ===================================================
pause