"""The settings file of a local install, edited from inside the app.

A downloaded AURA has no administrator with a terminal, so the few settings a
person needs — their model key, remote access — are set on screen and written
here: the desktop app's ~/.aura/aura.env, or the project's .env when AURA runs
from source. The file is read at startup, and the in-app screens also apply
what they write to the running process where that's safe.

The file lives in a folder AURA's own file tools are fenced out of, so the
assistant can't rewrite its own limits. Values are never read back out over
the API; only whether a key is set, and a hint of it.
"""

from __future__ import annotations

import os
from pathlib import Path

from app import envfile
from app.config import settings

hint = envfile.hint


def config_path() -> Path:
    if settings.aura_config_file:
        return Path(settings.aura_config_file).expanduser()
    # backend/app/services/local_config.py → the project root's .env
    return Path(__file__).resolve().parents[3] / ".env"


def read_values() -> dict[str, str]:
    return envfile.read_values(config_path())


def write_values(updates: dict[str, str | None]) -> Path:
    return envfile.write_values(updates, config_path())


def is_writable() -> bool:
    """Can the in-app screens save to the settings file?"""
    if not settings.local_mode:
        return False
    path = config_path()
    target = path if path.exists() else path.parent
    return target.exists() and os.access(target, os.W_OK)
