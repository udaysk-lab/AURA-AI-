"""The environment programs started by AURA get (review finding F2).

A command AURA runs for the user, or an app it opens, must not be able to read AURA's own
configuration out of its environment: `printenv` would otherwise show the sign-in key, the
signing secret, the token-encryption key and every model key. Children get the user's own
environment minus every variable AURA itself reads as configuration:

* every settings field of `app.config.Settings` (any letter case: the settings loader is
  case-insensitive, so `secret_key` counts as much as `SECRET_KEY`);
* the other variables AURA reads from the environment (database aliases, the port, ...);
* every variable named in AURA's settings files (`aura.env`, `.env`, `../.env`), whatever its name;
* everything starting with `AURA_`.

Everything else (PATH, HOME, the user's own variables) is kept. The two marker variables the
process guard looks for are added after the strip, by the caller.
"""

from __future__ import annotations

import os
from pathlib import Path

# Variables AURA reads straight from the environment (not through Settings fields).
EXTRA_NAMES = frozenset({
    "DATABASE_URL_OVERRIDE", "POSTGRES_URL", "POSTGRES_PRISMA_URL", "DATABASE_URL_UNPOOLED",
    "POSTGRES_URL_NON_POOLING", "MIGRATE_ON_PREVIEW", "AUTO_CREATE_SCHEMA", "AURA_PORT",
    "AURA_BIND_HOST", "AURA_TLS", "AURA_URL", "AURA_TOKEN",
})
PREFIXES = ("AURA_",)


def _file_names() -> set[str]:
    """Variable names written in AURA's settings files, wherever they are."""
    from app import envfile
    from app.config import settings

    paths: list[Path] = []
    if settings.aura_config_file.strip():
        paths.append(Path(settings.aura_config_file.strip()).expanduser())
    paths.append(Path(settings.aura_home_path) / "aura.env")
    here = Path(os.getcwd())
    backend = Path(__file__).resolve().parents[2]
    for base in (here, here.parent, backend, backend.parent):
        paths.append(base / ".env")
    names: set[str] = set()
    for path in paths:
        try:
            names.update(k.upper() for k in envfile.read_values(path))
        except Exception:  # an unreadable file hides nothing it does not name
            continue
    return names


def aura_names() -> set[str]:
    """Upper-case names of every variable that is AURA's configuration."""
    from app.config import Settings

    names = {f.upper() for f in Settings.model_fields} | set(EXTRA_NAMES)
    names |= _file_names()
    return names


def is_aura_variable(name: str, names: set[str] | None = None) -> bool:
    up = name.upper()
    return up.startswith(PREFIXES) or up in (names if names is not None else aura_names())


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The user's environment without AURA's configuration, plus `extra` (set after the strip)."""
    names = aura_names()
    env = {k: v for k, v in os.environ.items() if not is_aura_variable(k, names)}
    if extra:
        env.update(extra)
    return env
