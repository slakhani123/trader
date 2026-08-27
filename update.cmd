@echo off
rem One-click daily update: latest code, fresh market data, research scan.
title Vigil update
cd /d "%~dp0"
echo === Getting the latest code...
git pull
cd backend
echo === Fetching new market data (only what's new)...
.venv\Scripts\vigil seed
echo === Running the research scan...
.venv\Scripts\vigil scan
echo === Health summary...
.venv\Scripts\vigil health
echo.
echo Done. You can close this window.
pause
