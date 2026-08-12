@echo off
cd /d "%~dp0"

rem remove my own scratch files so they are never committed
del /q "%~dp0_git-status-log.txt" 2>nul
del /q "%~dp0_git-status.bat" 2>nul
del /q "%~dp0_git-ig-log.txt" 2>nul
del /q "%~dp0_git-ig.bat" 2>nul
del /q "%~dp0_git-ts-log.txt" 2>nul

set LOG=%~dp0_git-new-log.txt

(
  echo === COMMIT NEW WORK ===
  git add -A
  echo.
  echo --- staged ---
  git diff --cached --name-status
  echo.
  git commit -m "Add Render blueprint and restructure deployment config" -m "render.yaml deploys the FastAPI backend with Postgres and a cron worker; frontend stays on Vercel. Adds per-service env templates, rewrites bare postgresql:// URLs to the psycopg 3 dialect, gives worker.py a --once mode for platform cron, and updates DEPLOY.md for the split topology."
  echo.
  git push origin main
  echo.
  echo --- final state ---
  git log --oneline -3
  git status --short --branch
  echo === COMPLETE ===
) > "%LOG%" 2>&1

timeout /t 5 >nul
(goto) 2>nul & del "%~f0"
