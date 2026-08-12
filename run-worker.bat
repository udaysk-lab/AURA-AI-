@echo off
title AURA worker
cd /d "%~dp0backend"
call ".venv\Scripts\activate.bat"
echo.
echo   AURA worker — automations, schedules, heartbeat, memory compaction.
echo   Ticks every 60 seconds. The API does none of this on its own.
echo.
python worker.py
echo.
echo Worker stopped.
pause
