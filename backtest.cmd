@echo off
rem One-click point-in-time backtest with a one-year untouched holdout.
title Vigil backtest
cd /d "%~dp0backend"
.venv\Scripts\vigil backtest --start 2021-06-01 --holdout-start 2025-06-01
echo.
echo Done — open the Backtests tab on the dashboard to explore the results.
pause
