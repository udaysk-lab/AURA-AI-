@echo off
title AURA backend
cd /d "%~dp0backend"
call ".venv\Scripts\activate.bat"
echo Starting API on http://localhost:8000  (docs at /docs)
python -m uvicorn app.main:app --reload --port 8000
echo.
echo Backend stopped.
pause
