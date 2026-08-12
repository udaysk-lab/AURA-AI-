@echo off
title AURA AI launcher
cd /d "%~dp0"

echo ===============================
echo   AURA AI - local launcher
echo ===============================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [X] Python is not on PATH. Install Python 3.11+ and re-run.
  pause
  exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
  echo [X] Node.js / npm is not on PATH. Install Node 18+ and re-run.
  pause
  exit /b 1
)

echo [0/4] Clearing ports 3000 / 3001 / 8000...
set "AURA_KILLED="
for %%P in (3000 3001 8000) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /r /c:"TCP.*:%%P .*LISTENING"') do (
    taskkill /f /pid %%I >nul 2>&1
    if not errorlevel 1 (
      echo     stopped a process on port %%P
      set "AURA_KILLED=1"
    )
  )
)
if defined AURA_KILLED (
  if exist "frontend\.next" (
    echo     clearing stale Next.js build cache...
    rmdir /s /q "frontend\.next" >nul 2>&1
  )
)

echo [1/4] Backend virtual environment...
if not exist "backend\.venv\Scripts\python.exe" (
  python -m venv "backend\.venv"
  if errorlevel 1 (
    echo [X] Could not create the virtual environment.
    pause
    exit /b 1
  )
)

echo [2/4] Backend dependencies ^(first run takes a few minutes^)...
"backend\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
"backend\.venv\Scripts\python.exe" -m pip install -r "backend\requirements.txt"
if errorlevel 1 (
  echo [X] pip install failed - see the errors above.
  pause
  exit /b 1
)

echo [3/4] Frontend dependencies...
if not exist "frontend\node_modules" (
  pushd frontend
  call npm install
  if errorlevel 1 (
    echo [X] npm install failed - see the errors above.
    popd
    pause
    exit /b 1
  )
  popd
)
if not exist "frontend\.env.local" (
  echo NEXT_PUBLIC_API_URL=http://localhost:8000> "frontend\.env.local"
)

echo [4/4] Starting servers...
start "AURA backend" cmd /k ""%~dp0_aura-backend.bat""
timeout /t 3 /nobreak >nul
start "AURA frontend" cmd /k ""%~dp0_aura-frontend.bat""

echo.
echo   API   http://localhost:8000/docs
echo   App   http://localhost:3000
echo.
echo Two windows opened - leave them running. Close them to stop AURA.
timeout /t 20
