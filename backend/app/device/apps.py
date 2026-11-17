"""Launching and closing applications.

`open_app` opens only what the permission gate judged for the call: the real path of a file or folder, an http or
https URL, or the directory entry a bare application name resolved to (app/device/programs.py). It never hands the
text it was given to the system to look up, except for a name the owner approved after AURA said it could not find
it. `close_app` is a best-effort close by name.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

from app.device import childenv, filetypes, files, programs
from app.device.runtime import DeviceUnavailable, hidden_process, require_enabled


# Characters that mean something to a shell or to a process-name pattern. An
# app name never needs them, and without this an "app name" like
# `calc&del ...` or `*` would be a command, or every process, in disguise —
# and these tools run under a lower permission floor than run_command does.
_UNSAFE_NAME = set('&|<>^%!"`$;*?\n\r\t\x00')


# A NAME that ends like one of these is refused outright: opening it would run it, and that is what run_command is
# for. (A file of such a type given by path is judged by app/device/filetypes.py and asks the owner every time.)
_SCRIPTS = (
    ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
    ".hta", ".reg", ".py", ".pyw", ".sh", ".bash", ".zsh", ".command", ".tool",
    ".scpt", ".applescript", ".workflow", ".desktop", ".url", ".lnk", ".pif", ".scr",
    ".cpl", ".jar", ".appimage", ".run",
)
# Programs can only be named, not pointed at: "notepad.exe" is an installed
# app, "~/Downloads/setup.exe" is running whatever that file is.
_PROGRAMS = (".exe", ".com", ".msi", ".msp", ".app")


def _ends(target: str, suffixes: tuple[str, ...]) -> bool:
    return target.lower().rstrip("/\\ .").endswith(suffixes)


def _path_like(target: str) -> bool:
    return "/" in target or "\\" in target or target.startswith("~")


_RUN_IT = "Use run_command, which asks first."


def _check_name(target: str) -> str | None:
    if target.startswith("-"):
        return "An app name can't start with '-'."
    if any(ch in _UNSAFE_NAME for ch in target):
        return f"{target!r} doesn't look like an app name. {_RUN_IT}"
    if _ends(target, _SCRIPTS):
        return f"{target!r} is a script, and opening it would run it. {_RUN_IT}"
    if _path_like(target) and _ends(target, _PROGRAMS):
        return f"{target!r} is a program file, and opening it would run it. {_RUN_IT}"
    return None


def _windows_open(target: str) -> None:
    """ShellExecute, the thing `start` does — without a cmd.exe to parse the name."""
    os.startfile(target)  # type: ignore[attr-defined]


def open_decided_path(decided: str, label: str, *, owner_approved: bool) -> dict:
    """Open exactly the real path the gate decided on, after judging it again with the same function.

    A file whose type needs the owner opens only when the owner approved this very path; a path that changed
    place, or became something else since the decision, is refused. Nothing is looked up by name here."""
    require_enabled()
    try:
        real = files.resolve(decided, must_exist=True)
    except DeviceUnavailable as exc:
        return {"error": str(exc)}
    if os.path.normcase(real) != os.path.normcase(decided):
        return {"error": f"{label} no longer points where it did when this was checked, so nothing was opened."}
    kind = filetypes.classify(real)
    if kind.kind == "missing":
        return {"error": f"{label} does not exist any more, so nothing was opened."}
    if kind.kind == "unchecked" and not owner_approved:
        return {"error": f"{label} is not a document or media file, and opening it may run it. "
                         "It needs your approval each time."}
    return _open_target(real, label=f"Opened {label}")


def _start_judged(target: str, program: str, owner_approved: bool) -> dict:
    """Start a bare application name the gate judged: exactly the entry it judged, after resolving and judging the
    name again with the same functions. A different answer than the one judged is refused."""
    from app.permissions import access, floors

    now = programs.resolve(target)
    if os.path.normcase(now) != os.path.normcase(program):
        return {"error": f"{target!r} no longer leads to the program it did when this was checked, so nothing was started."}
    platform = programs.current_platform()
    verdict = programs.judge(now, access.roots(), floors.in_self_path, platform)
    if verdict.kind == "self":
        return {"error": f"{target!r} is part of AURA itself, so nothing was started."}
    if verdict.kind == "nothing":
        return {"error": f"Couldn't find an app called {target!r} on PATH."}
    if verdict.kind == "owner" and not owner_approved:
        return {"error": f"{target!r} needs your approval each time, and it was not given for this program, "
                         "so nothing was started."}
    try:
        if not now:
            # The owner approved a name AURA could not find: the system looks it up, as it always did.
            if platform == programs.MACOS:
                subprocess.run(["open", "-a", target], check=True, env=childenv.child_env())
            else:
                _windows_open(target)
        elif platform == programs.MACOS:
            subprocess.run(["open", "-a", now], check=True, env=childenv.child_env())
        elif platform == programs.WINDOWS:
            _windows_open(now)
        else:
            subprocess.Popen(
                [now], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
                env=childenv.child_env(),
            )
        return {"message": f"Launched {target}.", "app": target}
    except (subprocess.CalledProcessError, OSError):
        return {"error": f"Couldn't launch {target!r}. Check the name and that it's installed."}


def open_app(name: str, *, decided: str | None = None, owner_approved: bool = False,
             program: str | None = None) -> dict:
    """Open what the permission gate judged for this call, and nothing else.

    `decided` is the canonical real path the gate judged for a file or folder argument: that exact path is opened,
    never the text again. `program` is the directory entry the gate resolved a bare application name to ("" when it
    found none): that exact entry is started, after resolving and judging the name again. With neither, nothing was
    judged: an argument that names a file now is refused as not what was checked, and a name is treated as one the
    gate found nothing for, so it starts only if it still resolves to nothing and the owner approved that."""
    require_enabled()
    target = (name or "").strip()
    if not target:
        return {"error": "No application given."}
    if decided is not None:
        return open_decided_path(decided, target, owner_approved=owner_approved)
    if os.path.exists(os.path.expanduser(target)) and not target.lower().startswith(("http://", "https://")):
        return {"error": f"{target!r} is a file now, not what was checked, so nothing was opened."}

    if target.lower().startswith(("http://", "https://")):
        if any(ch in target for ch in '"\n\r\x00') or " " in target:
            return {"error": "That isn't a URL I can open."}
        return _open_target(target, label=f"Opened {target}")

    if re.match(r"[A-Za-z][A-Za-z0-9+.\-]+:", target):
        return {"error": f"{target!r} doesn't look like an app name. Only http and https links can be opened."}
    problem = _check_name(target)
    if problem:
        return {"error": problem}
    return _start_judged(target, program or "", owner_approved)


def _open_target(target: str, label: str) -> dict:
    """Open a document (already fenced) or a URL with its default app."""
    if target.startswith("-"):
        return {"error": "That isn't something I can open."}
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", target], check=False, env=childenv.child_env())
        elif os.name == "nt":
            _windows_open(target)
        elif shutil.which("xdg-open"):
            subprocess.Popen(
                ["xdg-open", target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
                env=childenv.child_env(),
            )
        else:
            return {"error": "No opener available (install xdg-utils)."}
    except OSError as exc:
        return {"error": f"Couldn't open it: {exc}"}
    return {"message": label, "target": target}


def close_app(name: str) -> dict:
    """Best-effort close of an app by name. Asks the OS to end its processes."""
    require_enabled()
    target = (name or "").strip()
    if not target:
        return {"error": "No application given."}
    if target.startswith("-") or any(ch in _UNSAFE_NAME for ch in target):
        return {"error": f"{target!r} doesn't look like an app name."}
    try:
        if sys.platform == "darwin":
            # The name is an argument to the script, never part of its text.
            subprocess.run(
                ["osascript", "-e", "on run argv", "-e", "quit app (item 1 of argv)",
                 "-e", "end run", target],
                check=False,
            )
        elif os.name == "nt":
            exe = target if target.lower().endswith(".exe") else f"{target}.exe"
            subprocess.run(
                ["taskkill", "/IM", exe, "/F"], capture_output=True, check=False, **hidden_process()
            )
        else:
            # Exact process name, not a pattern over every command line: "-f ."
            # would have matched everything this user runs.
            subprocess.run(["pkill", "-x", "--", target[:15]], check=False)
    except Exception as exc:
        return {"error": f"Couldn't close {target!r}: {exc}"}
    return {"message": f"Asked {target} to close.", "app": target}


def list_applications(limit: int = 60) -> list[dict]:
    """Installed / available applications, best effort per platform."""
    require_enabled()
    apps: list[str] = []
    try:
        if sys.platform == "darwin":
            for base in ("/Applications", os.path.expanduser("~/Applications")):
                if os.path.isdir(base):
                    apps += [f[:-4] for f in os.listdir(base) if f.endswith(".app")]
        elif os.name == "nt":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-StartApps | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=20, **hidden_process(),
            ).stdout
            apps = [line.strip() for line in out.splitlines() if line.strip()]
        else:
            seen = set()
            for d in ("/usr/share/applications", os.path.expanduser("~/.local/share/applications")):
                if os.path.isdir(d):
                    for f in os.listdir(d):
                        if f.endswith(".desktop"):
                            seen.add(f[:-8])
            apps = sorted(seen)
    except Exception:
        return []
    apps = sorted(set(apps))[:limit]
    return [{"name": a} for a in apps]
