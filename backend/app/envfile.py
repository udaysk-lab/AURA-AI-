"""Reading and writing KEY=value settings files, with nothing else attached.

Kept free of app.config on purpose: the desktop launcher reads AURA's settings
file *before* the configuration is loaded, because what it finds there decides
that configuration.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


def quote(value: str) -> str:
    if value == "" or re.search(r"[\s#\"'\\]", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _key_of(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return ""
    key = stripped.split("=", 1)[0].strip()
    if key.startswith("export "):
        key = key[len("export "):].strip()
    return key


def read_values(path: Path) -> dict[str, str]:
    """KEY=value pairs from the file, ignoring comments. Missing file → {}."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for raw in text.splitlines():
        key = _key_of(raw)
        if not key:
            continue
        value = raw.strip().split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        else:
            # An unquoted value ends at an inline comment.
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


def write_values(updates: dict[str, str | None], path: Path) -> Path:
    """Set (or, with None, remove) keys in the file, keeping everything else.

    Comments and the order of existing lines survive, so a file someone has
    edited by hand stays readable. Written atomically, readable only by the
    owner where the platform has such a thing.
    """
    for key, value in updates.items():
        if not _KEY.match(key):
            raise ValueError(f"Not a settings name: {key!r}")
        if value is not None and ("\n" in value or "\r" in value):
            raise ValueError(f"{key} can't contain a line break")

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = []

    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        key = _key_of(line)
        if key and key in remaining:
            value = remaining.pop(key)
            if value is not None:
                out.append(f"{key}={quote(value)}")
            continue
        out.append(line)

    additions = [(k, v) for k, v in remaining.items() if v is not None]
    if additions:
        if out and out[-1].strip():
            out.append("")
        out.extend(f"{key}={quote(value)}" for key, value in additions)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def hint(secret: str) -> str:
    """Enough of a key to recognise it, never enough to use it."""
    secret = secret or ""
    if not secret:
        return ""
    if len(secret) <= 12:
        return "•" * 8
    return f"{secret[:7]}…{secret[-4:]}"
