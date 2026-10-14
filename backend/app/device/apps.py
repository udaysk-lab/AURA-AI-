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
from app.device.runtime import DeviceUnavailable, require_enabled


def open_app(name: str) -> dict:
    """Launch an application by name (or open a file/URL target)."""
    require_enabled()
    target = (name or "").strip()
    if not target:
        return {"error": "No application given."}

    # If it names a path inside the allowed roots, open it as a document.
    maybe_path = os.path.expanduser(target)
    if os.path.exists(maybe_path):
        try:
            resolved = files.resolve(maybe_path, must_exist=True)
            return _open_target(resolved, label=f"Opened {target}")
        except DeviceUnavailable:
            pass  # fall through to treat it as an app name

    if target.startswith(("http://", "https://")):
        return _open_target(target, label=f"Opened {target}")

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-a", target], check=True)
        elif os.name == "nt":
            # `start` is a cmd builtin; the empty title arg keeps quoted names working.
            subprocess.run(["cmd", "/c", "start", "", target], check=True)
        else:
            exe = shutil.which(target)
            if not exe:
                return {"error": f"Couldn't find an app called {target!r} on PATH."}
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"message": f"Launched {target}.", "app": target}
    except subprocess.CalledProcessError:
        return {"error": f"Couldn't launch {target!r}. Check the name and that it's installed."}


def _open_target(target: str, label: str) -> dict:
    if sys.platform == "darwin":
        subprocess.run(["open", target], check=False)
    elif os.name == "nt":
        subprocess.run(["cmd", "/c", "start", "", target], check=False)
    elif shutil.which("xdg-open"):
        subprocess.run(["xdg-open", target], check=False)
    else:
        return {"error": "No opener available (install xdg-utils)."}
    return {"message": label, "target": target}


def close_app(name: str) -> dict:
    """Best-effort close of an app by name. Asks the OS to end its processes."""
    require_enabled()
    target = (name or "").strip()
    if not target:
        return {"error": "No application given."}
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e", f'quit app "{target}"'], check=False)
        elif os.name == "nt":
            exe = target if target.lower().endswith(".exe") else f"{target}.exe"
            subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True, check=False)
        else:
            subprocess.run(["pkill", "-f", target], check=False)
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
                capture_output=True, text=True, timeout=20,
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
