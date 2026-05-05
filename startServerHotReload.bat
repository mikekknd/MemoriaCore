@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set PYTHON=venv_ai_memory\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

"%PYTHON%" run_server_hot_reload.py

pause
