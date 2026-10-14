#!/usr/bin/env bash
#
# AURA from a terminal. Pass your question as the argument:
#
#     ./aura.sh "what's on my calendar today"
#     ./aura.sh --status
#     ./aura.sh                              (interactive)
#
# Needs the backend running — ./aura-local.sh does that.
set -euo pipefail
cd "$(dirname "$0")/backend"
if [[ ! -x .venv/bin/python ]]; then
  echo "The backend environment isn't set up yet. Run ./aura-local.sh once first." >&2
  exit 1
fi
exec .venv/bin/python aura_cli.py "$@"
