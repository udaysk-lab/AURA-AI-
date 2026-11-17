"""Which program would start for a bare application name, and whether AURA may start it without asking.

`open_app("notes")` used to hand the name to the system (a PATH lookup, ShellExecute, LaunchServices), which finds
whatever is there, including a file the agent wrote into a folder that is on PATH. Now AURA looks the name up itself
(`resolve`), judges the directory entry it found (`judge`), and starts exactly that entry. The permission gate and the
handler call the same two functions, so a decision and the action cannot disagree.

`resolve` answers with the absolute path of the entry as found (links are not followed), or "" when nothing is found.

* Linux and other POSIX: `shutil.which`.
* Windows: `shutil.which` (PATH and PATHEXT), then the App Paths registry (current user, then this machine).
* macOS: an application bundle named `<name>.app`, matched without case and then by canonical application id,
  directly inside one of APPLICATION_ROOTS or one folder below it.

`judge` says what the answer needs: "ok" (follows the mode and the rules), "owner" (the owner is asked every time:
the entry, its true place or the real path it leads to is inside a connected folder, or nothing was found on Windows or macOS and
the system would look the name up itself), "nothing" (nothing found on Linux: nothing starts) or "self" (inside
AURA's own paths: refused).
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Callable

from app.device import appnames, files

LINUX, WINDOWS, MACOS = "linux", "windows", "macos"

# Where macOS keeps application bundles. A module constant so that tests can replace it.
APPLICATION_ROOTS = ("/Applications", "/System/Applications", "/System/Library/CoreServices", "~/Applications")

_APP_PATHS = r"Software\Microsoft\Windows\CurrentVersion\App Paths"


def current_platform() -> str:
    """The platform branch to use (a function so that tests can switch it)."""
    if sys.platform == "darwin":
        return MACOS
    if os.name == "nt":
        return WINDOWS
    return LINUX


# --- the resolver -----------------------------------------------------------


def _registry_values(name: str) -> list[str]:
    """The App Paths values for a name, current user first, then this machine (Windows only; tests replace it)."""
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return []
    keys = [name] if name.lower().endswith(".exe") else [name + ".exe", name]
    out: list[str] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key in keys:
            try:
                with winreg.OpenKey(hive, _APP_PATHS + "\\" + key) as handle:
                    value, _kind = winreg.QueryValueEx(handle, "")
            except OSError:
                continue
            if isinstance(value, str) and value.strip():
                out.append(value)
    return out


def _expand_variables(text: str) -> str:
    def one(m: re.Match) -> str:
        found = os.environ.get(m.group(1))
        if found is None:
            found = next((v for k, v in os.environ.items() if k.lower() == m.group(1).lower()), None)
        return found if found is not None else m.group(0)

    return re.sub(r"%([^%]+)%", one, text)


def _registry_file(raw: str) -> str:
    text = raw.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        text = text[1:end] if end > 0 else text[1:]
    text = _expand_variables(text.strip().strip('"').strip())
    if text and os.path.isabs(text) and os.path.isfile(text):
        return os.path.abspath(text)
    return ""


def _which(name: str) -> str:
    found = shutil.which(name)
    return os.path.abspath(found) if found else ""


def _macos_bundle(name: str) -> str:
    wanted = name[:-4] if name.lower().endswith(".app") else name
    wanted_id = appnames.canonical(wanted)
    candidates: list[tuple[str, str]] = []  # (folder, bundle file name), roots in order, one level below too
    for root in APPLICATION_ROOTS:
        base = os.path.abspath(os.path.expanduser(root))
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            continue
        folders = [base]
        for entry in entries:
            if entry.lower().endswith(".app"):
                candidates.append((base, entry))
            elif os.path.isdir(os.path.join(base, entry)):
                folders.append(os.path.join(base, entry))
        for folder in folders[1:]:
            try:
                below = sorted(os.listdir(folder))
            except OSError:
                continue
            candidates.extend((folder, e) for e in below if e.lower().endswith(".app"))
    for test in (lambda stem: stem.lower() == wanted.lower(),
                 lambda stem: bool(wanted_id) and appnames.canonical(stem) == wanted_id):
        for folder, entry in candidates:
            if test(entry[:-4]) and os.path.isdir(os.path.join(folder, entry)):
                return os.path.join(folder, entry)
    return ""


def resolve(name: str) -> str:
    """The absolute path of the directory entry that would start for this bare name, or "" (links not followed)."""
    text = (name or "").strip()
    if not text:
        return ""
    kind = current_platform()
    if kind == MACOS:
        return _macos_bundle(text)
    found = _which(text)
    if found or kind != WINDOWS:
        return found
    for raw in _registry_values(text):
        entry = _registry_file(raw)
        if entry:
            return entry
    return ""


# --- the judgement ----------------------------------------------------------


@dataclass(frozen=True)
class Judgement:
    kind: str  # ok | owner | nothing | self
    why: str = ""  # for "owner": "folder" (inside a folder AURA can change) or "unfound"


def _inside(path: str, roots) -> bool:
    return any(files._within(path, root) for root in roots)


def judge(entry: str, roots, self_check: Callable[[str], bool], platform: str | None = None) -> Judgement:
    """What this resolved entry ("" when nothing was found) needs. `roots` are the user's connected folders and
    `self_check` says whether a real path is one of AURA's own."""
    platform = platform or current_platform()
    if not entry:
        return Judgement("nothing") if platform == LINUX else Judgement("owner", "unfound")
    try:
        real = os.path.realpath(entry)
        # The true place of the entry itself: its folder with links followed, its own name kept. A PATH folder spelled
        # through a link lives where the link leads, even when the entry in it is a link that leads somewhere else.
        place = os.path.join(os.path.realpath(os.path.dirname(entry)), os.path.basename(entry))
    except (OSError, ValueError):
        return Judgement("owner", "folder")  # cannot be judged: ask
    forms = (entry, place, real)
    if any(self_check(p) for p in forms):
        return Judgement("self")
    if any(_inside(p, roots) for p in forms):
        return Judgement("owner", "folder")
    return Judgement("ok")
