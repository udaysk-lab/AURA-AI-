@echo off
REM AURA from a terminal. Pass your question as the argument:
REM
REM     aura "what's on my calendar today"
REM     aura --status
REM     aura                              (interactive)
REM
REM Needs the backend running — start-aura-local.bat does that.
setlocal
cd /d "%~dp0backend"
if not exist ".venv\Scripts\python.exe" (
    echo The backend environment isn't set up yet. Run start-aura-local.bat once first.
    exit /b 1
)
".venv\Scripts\python.exe" aura_cli.py %*
