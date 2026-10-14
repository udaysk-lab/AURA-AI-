#!/usr/bin/env bash
#
# AURA, running on this computer (macOS / Linux).
#
# This launch gives AURA your hands on THIS machine: it can run commands, read
# and write your files, open apps, watch the system, and see the screen.
# Commands and screen control ask for your approval every time by default.
#
#   ./aura-local.sh            start everything
#   ./aura-local.sh --no-web   backend only (for the CLI)
#
set -euo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; OFF=$'\033[0m'
say()  { printf '%s\n' "  $*"; }
fail() { printf '%s\n' "  ${RED}$*${OFF}" >&2; exit 1; }

WITH_WEB=1
[[ "${1:-}" == "--no-web" ]] && WITH_WEB=0

printf '\n  %sAURA — starting on this computer%s\n' "$BOLD" "$OFF"
printf '  %s===============================%s\n\n' "$DIM" "$OFF"

# --- Python -----------------------------------------------------------------
PYTHON="$(command -v python3 || command -v python || true)"
[[ -n "$PYTHON" ]] || fail "Python 3.11+ isn't installed. Install it and run this again."

if [[ ! -x backend/.venv/bin/python ]]; then
  say "[1/5] Creating the Python environment (one time)…"
  "$PYTHON" -m venv backend/.venv || fail "Could not create the virtual environment."
else
  say "[1/5] Python environment found."
fi
VENV=backend/.venv/bin/python

say "[2/5] Backend packages…"
"$VENV" -m pip install --quiet --upgrade pip
"$VENV" -m pip install --quiet -r backend/requirements.txt || fail "pip install failed."

# Device extras are best effort: AURA still runs commands and handles files
# without them, and tells you which are missing. On Linux these also want
# system packages — see backend/requirements-device.txt.
say "[3/5] Device extras (screen control, process stats, clipboard)…"
if ! "$VENV" -m pip install --quiet -r backend/requirements-device.txt 2>/dev/null; then
  say "      ${DIM}(some extras didn't install — AURA will say which are missing)${OFF}"
fi

# --- Frontend ---------------------------------------------------------------
if [[ $WITH_WEB -eq 1 ]]; then
  command -v npm >/dev/null || fail "Node.js / npm isn't installed. Install Node 18+ and run this again."
  if [[ ! -d frontend/node_modules ]]; then
    say "[4/5] Frontend packages (one time)…"
    (cd frontend && npm install --silent) || fail "npm install failed."
  else
    say "[4/5] Frontend packages found."
  fi
  [[ -f frontend/.env.local ]] || echo "NEXT_PUBLIC_API_URL=http://127.0.0.1:8000" > frontend/.env.local
else
  say "[4/5] Skipping the web app (--no-web)."
fi

# --- Launch -----------------------------------------------------------------
# LOCAL_MODE turns on device control, the single-owner sign-in and the
# in-process worker. Set here rather than written into your .env, so this
# script is the only thing that enables it.
#
# 127.0.0.1 is deliberate: bound to loopback, nothing on your network can reach
# the API — and the API is the surface that can operate this machine.
export LOCAL_MODE=true
export ENVIRONMENT=local
# Device work takes more steps than a chat answer — look at the screen, click,
# look again — so raise the per-turn step budget from the hosted default of 8.
# Set MAX_AGENT_STEPS in .env to override.
export MAX_AGENT_STEPS=16

say "[5/5] Starting AURA…"
printf '\n'

PIDS=()
cleanup() {
  printf '\n  Stopping…\n'
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  printf '  Stopped.\n'
}
trap cleanup EXIT INT TERM

(cd backend && exec ../"$VENV" -m uvicorn app.main:app --host 127.0.0.1 --port 8000) &
PIDS+=($!)

# Give the API a head start so the first paint isn't an error about reaching it.
sleep 4

if [[ $WITH_WEB -eq 1 ]]; then
  (cd frontend && exec npm run dev) &
  PIDS+=($!)
  sleep 6
  OPENER="$(command -v open || command -v xdg-open || true)"
  [[ -n "$OPENER" ]] && "$OPENER" "http://localhost:3000" >/dev/null 2>&1 || true
fi

cat <<INFO

  ${GREEN}Running.${OFF}
    Web app        http://localhost:3000
    API            http://127.0.0.1:8000        ${DIM}(docs at /docs)${OFF}
    This computer  http://localhost:3000/device
    What's set up  http://localhost:3000/setup

  Press "Open AURA on this computer" on the sign-in screen — a local install
  needs no account.

  From a terminal:  ./aura.sh "what's on my calendar"

  ${BOLD}Worth knowing${OFF}
    • Commands and screen control ask for approval every time. Choose "Always"
      on a card to stop being asked for that one action, or raise your trust
      tier in Settings.
    • By default AURA may read and write anything under your home folder.
      Narrow that with DEVICE_ALLOWED_ROOTS in .env — see .env.example.
    • It will not touch ~/.ssh, ~/.aws or the other credential folders.
    • On macOS, screen control asks for Accessibility and Screen Recording
      permission the first time — grant it to the terminal you launched from.

  Ctrl+C to stop.

INFO

wait
