@echo off
:: 清掉佔用 8088 埠的舊進程（PowerShell 隔離，避免 taskkill 把 Ctrl+C 傳回 batch）
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8088 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

venv_ai_memory\Scripts\python.exe run_server.py