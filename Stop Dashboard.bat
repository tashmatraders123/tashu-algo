@echo off
cd /d "%~dp0"

echo Stopping the bot (if running)...
curl -s -X POST http://127.0.0.1:5000/api/stop >nul 2>&1
timeout /t 2 /nobreak >nul

if exist server.pid (
    for /f %%p in (server.pid) do (
        echo Stopping dashboard server...
        taskkill /PID %%p /F >nul 2>&1
    )
    del server.pid >nul 2>&1
)

echo Done. You can close this window.
pause
