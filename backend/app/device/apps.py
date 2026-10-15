"""Launching and closing applications.

"Open Spotify", "open this folder", "open that URL in the browser" — the
cross-platform launchers for each OS, plus a best-effort close. Opening a
document or URL goes through the same per-platform opener (`open`, `start`,
`xdg-open`), with any file path fenced to the allowed roots first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from app.device import files
from app.device.runtime import DeviceUnavailable, hidden_process, require_enabled


# Characters that mean something to a shell or to a process-name pattern. An
# app name never needs them, and without this an "app name" like
# `calc&del ...` or `*` would be a command, or every process, in disguise —
# and these tools run at a lower trust tier than run_command does.
_UNSAFE_NAME = set('&|<>^%!"`$;*?\n\r\t\x00')


# Opening one of these runs it, and that's what run_command is for — which
# asks first, where opening an app doesn't at relaxed trust. Scripts are plain
# text, so the file tools could write one; they're never opened, by path or by
# name (a name is looked up on PATH, which can include folders in your home).
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


def open_app(name: str) -> dict:
    """Launch an application by name (or open a file/URL target)."""
    require_enabled()
    target = (name or "").strip()
    if not target:
        return {"error": "No application given."}

    # If it names a path inside the allowed roots, open it as a document —
    # unless opening it would run it.
    maybe_path = os.path.expanduser(target)
    if os.path.exists(maybe_path):
        executable = os.name != "nt" and os.path.isfile(maybe_path) and os.access(maybe_path, os.X_OK)
        if _ends(maybe_path, _SCRIPTS + _PROGRAMS) or executable:
            return {"error": f"{target!r} is a program or script, and opening it would run it. {_RUN_IT}"}
        try:
            resolved = files.resolve(maybe_path, must_exist=True)
            return _open_target(resolved, label=f"Opened {target}")
        except DeviceUnavailable:
            pass  # fall through to treat it as an app name

    if target.startswith(("http://", "https://")):
        if any(ch in target for ch in '"\n\r\x00') or " " in target:
            return {"error": "That isn't a URL I can open."}
        return _open_target(target, label=f"Opened {target}")

    problem = _check_name(target)
    if problem:
        return {"error": problem}
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-a", target], check=True)
        elif os.name == "nt":
            _windows_open(target)
        else:
            exe = shutil.which(target)
            if not exe:
                return {"error": f"Couldn't find an app called {target!r} on PATH."}
            subprocess.Popen(
                [exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
        return {"message": f"Launched {target}.", "app": target}
    except (subprocess.CalledProcessError, OSError):
        return {"error": f"Couldn't launch {target!r}. Check the name and that it's installed."}


def _open_target(target: str, label: str) -> dict:
    """Open a document (already fenced) or a URL with its default app."""
    if target.startswith("-"):
        return {"error": "That isn't something I can open."}
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", target], check=False)
        elif os.name == "nt":
            _windows_open(target)
        elif shutil.which("xdg-open"):
            subprocess.Popen(
                ["xdg-open", target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
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
