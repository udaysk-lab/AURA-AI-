@echo off
title AURA frontend
cd /d "%~dp0frontend"
echo Starting web app on http://localhost:3000
call npm run dev
echo.
echo Frontend stopped.
pause
