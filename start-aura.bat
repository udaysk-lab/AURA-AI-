@echo off
setlocal
title AURA launcher
cd /d "%~dp0"

echo.
echo   AURA — starting up
echo   ==================
echo.

REM --- Backend dependencies -------------------------------------------------
if not exist "backend\.venv\Scripts\python.exe" (
    echo   [1/4] Creating the Python environment ^(one time, ~1 min^)...
    python -m venv "backend\.venv"
    if errorlevel 1 (
        echo.
        echo   Could not create the virtual environment.
        echo   Install Python 3.11+ from python.org and tick "Add to PATH".
        echo.
        pause
        exit /b 1
    )
    call "backend\.venv\Scripts\activate.bat"
    echo   Installing backend packages...
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r "backend\requirements.txt"
) else (
    echo   [1/4] Python environment found.
)

REM --- Frontend dependencies ------------------------------------------------
if not exist "frontend\node_modules" (
    echo   [2/4] Installing frontend packages ^(one time, ~2 min^)...
    pushd frontend
    call npm install --silent
    if errorlevel 1 (
        popd
        echo.
        echo   npm install failed. Install Node 18+ from nodejs.org.
        echo.
        pause
        exit /b 1
    )
    popd
) else (
    echo   [2/4] Frontend packages found.
)

REM --- Config ---------------------------------------------------------------
REM Without this the browser falls back to localhost:8000 anyway, but being
REM explicit means the setting is visible rather than implied.
if not exist "frontend\.env.local" (
    echo NEXT_PUBLIC_API_URL=http://localhost:8000> "frontend\.env.local"
    echo   [3/4] Wrote frontend\.env.local
) else (
    echo   [3/4] Config found.
)

REM --- Launch ---------------------------------------------------------------
echo   [4/4] Starting API and web app in separate windows...
echo.

start "AURA backend" cmd /k "cd /d "%~dp0backend" && call ".venv\Scripts\activate.bat" && python -m uvicorn app.main:app --reload --port 8000"

REM Next.js is the slower of the two to become useful; give the API a head
REM start so the sign-in screen doesn't flash its "can't reach the API" error
REM on first paint.
timeout /t 4 /nobreak >nul

start "AURA frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"

echo   Waiting for the web app to compile...
timeout /t 12 /nobreak >nul
start "" "http://localhost:3000"

echo.
echo   Running.
echo     Web app   http://localhost:3000
echo     API       http://localhost:8000
echo     API docs  http://localhost:8000/docs
echo     Setup     http://localhost:3000/setup   ^(what's configured, what isn't^)
echo.
echo   Sign in with any email — demo login needs no password.
echo.
echo   Optional: for automations, schedules and the overnight heartbeat, run
echo   run-worker.bat in a third window.
echo.
echo   Close the two AURA windows to stop.
echo.
pause
