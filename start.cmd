@echo off
rem One-click launch: API window + dashboard window, then open the browser.
cd /d "%~dp0"
start "Vigil API" /D "%~dp0backend" cmd /k .venv\Scripts\vigil serve
start "Vigil dashboard" /D "%~dp0frontend" cmd /k npm run dev
echo Waiting for the dashboard to come up...
timeout /t 8 /nobreak >nul
start http://localhost:5173
