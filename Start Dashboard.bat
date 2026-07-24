@echo off
cd /d "%~dp0"

if not exist venv\Scripts\pythonw.exe (
    echo Could not find venv\Scripts\pythonw.exe
    echo Make sure you have already run: python -m venv venv
    echo and:                             pip install -r requirements.txt
    pause
    exit /b 1
)

del dashboard_server.log >nul 2>&1
start "" /B venv\Scripts\pythonw.exe app.py >> dashboard_server.log 2>&1
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:5000

