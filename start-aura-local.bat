@echo off
setlocal
title AURA — local assistant
cd /d "%~dp0"

echo.
echo   AURA — starting on this computer
echo   ===============================
echo.
echo   This launch gives AURA your hands on THIS machine: it can run commands,
echo   read and write your files, open apps, watch the system, and see the
echo   screen. Nothing happens without your say-so until you say otherwise —
echo   commands and screen control ask for approval every time by default.
echo.

REM --- Python ---------------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo   [X] Python isn't on PATH. Install Python 3.11+ from python.org
    echo       and tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

REM --- Backend environment --------------------------------------------------
if not exist "backend\.venv\Scripts\python.exe" (
    echo   [1/5] Creating the Python environment ^(one time, ~1 min^)...
    python -m venv "backend\.venv"
    if errorlevel 1 (
        echo   [X] Could not create the virtual environment.
        pause
        exit /b 1
    )
) else (
    echo   [1/5] Python environment found.
)

echo   [2/5] Backend packages...
"backend\.venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
"backend\.venv\Scripts\python.exe" -m pip install --quiet -r "backend\requirements.txt"
if errorlevel 1 (
    echo   [X] pip install failed — see the errors above.
    pause
    exit /b 1
)

REM Device extras: process stats, screen control, clipboard. Best effort —
REM AURA still runs commands and handles files without them, so a failure here
REM must not stop the launch.
echo   [3/5] Device extras ^(screen control, process stats, clipboard^)...
"backend\.venv\Scripts\python.exe" -m pip install --quiet -r "backend\requirements-device.txt"
if errorlevel 1 (
    echo         ^(some extras didn't install — AURA will say which are missing^)
)

REM --- Frontend -------------------------------------------------------------
where npm >nul 2>&1
if errorlevel 1 (
    echo   [X] Node.js / npm isn't on PATH. Install Node 18+ from nodejs.org.
    echo.
    pause
    exit /b 1
)
if not exist "frontend\node_modules" (
    echo   [4/5] Frontend packages ^(one time, ~2 min^)...
    pushd frontend
    call npm install --silent
    if errorlevel 1 (
        popd
        echo   [X] npm install failed — see the errors above.
        pause
        exit /b 1
    )
    popd
) else (
    echo   [4/5] Frontend packages found.
)
if not exist "frontend\.env.local" (
    echo NEXT_PUBLIC_API_URL=http://127.0.0.1:8000> "frontend\.env.local"
)

REM --- Launch ---------------------------------------------------------------
REM LOCAL_MODE is what turns on device control, the single-owner sign-in, and
REM the in-process worker. It's set here rather than written into your .env so
REM this launcher is the only thing that enables it — run the ordinary
REM start-aura.bat and AURA goes back to being chat-only.
REM
REM 127.0.0.1 is deliberate: bound to loopback, nothing on your network can
REM reach the API, which is the surface that can operate this machine.
REM
REM Device work takes more steps than a chat answer — look at the screen,
REM click, look again — so the step budget per turn is raised from the
REM hosted default of 8. Override it in .env if you want.
echo   [5/5] Starting AURA...
echo.

start "AURA backend (local)" cmd /k "cd /d "%~dp0backend" && set LOCAL_MODE=true&& set ENVIRONMENT=local&& set MAX_AGENT_STEPS=16&& call ".venv\Scripts\activate.bat" && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

REM Next.js takes longer to become useful; let the API get ahead so the first
REM paint isn't an error about not reaching it.
timeout /t 5 /nobreak >nul

start "AURA web app" cmd /k "cd /d "%~dp0frontend" && npm run dev"

echo   Waiting for the web app to compile...
timeout /t 12 /nobreak >nul
start "" "http://localhost:3000"

echo.
echo   Running.
echo     Web app       http://localhost:3000
echo     API           http://127.0.0.1:8000       ^(docs at /docs^)
echo     This computer http://localhost:3000/device
echo     What's set up http://localhost:3000/setup
echo.
echo   Press "Open AURA on this computer" on the sign-in screen — a local
echo   install needs no account.
echo.
echo   From a terminal:  aura.bat "what's on my calendar"
echo.
echo   Worth knowing:
echo     * Commands and screen control ask for approval every time. Choose
echo       "Always" on a card to stop being asked for that one action, or raise
echo       your trust tier in Settings.
echo     * By default AURA may read and write anything under your user folder.
echo       Narrow that with DEVICE_ALLOWED_ROOTS in .env — see .env.example.
echo     * It will not touch ~\.ssh, ~\.aws and the other credential folders.
echo.
echo   Close the two AURA windows to stop it.
echo.
pause
