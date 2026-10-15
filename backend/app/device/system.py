"""System state and the small desktop niceties.

Process inspection, clipboard, desktop notifications, and a system snapshot
(CPU, memory, disk, battery). The heavier pieces lean on `psutil` when it's
installed and degrade to a plain message when it isn't, so the tool never dies
in a way the agent can't report.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from app.device.runtime import DeviceUnavailable, hidden_process, require_enabled


def _psutil():
    try:
        import psutil  # type: ignore

        return psutil
    except ImportError:
        raise DeviceUnavailable(
            "Process and system stats need the 'psutil' package. "
            "Install the device extras: pip install -r requirements-device.txt"
        )


# ---------------------------------------------------------------------------
# System snapshot
# ---------------------------------------------------------------------------


def system_info() -> dict:
    require_enabled()
    info: dict = {}
    try:
        psutil = _psutil()
    except DeviceUnavailable:
        # Still give the cheap os-level facts without psutil.
        import platform

        return {
            "os": platform.platform(),
            "note": "Install psutil for CPU/memory/disk detail.",
            "cpu_count": os.cpu_count(),
        }

    vm = psutil.virtual_memory()
    home = os.path.expanduser("~")
    disk = psutil.disk_usage(home if os.path.isdir(home) else os.path.abspath(os.sep))
    info = {
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "cpu_count": psutil.cpu_count(),
        "memory": {
            "total_gb": round(vm.total / 1e9, 1),
            "used_gb": round(vm.used / 1e9, 1),
            "percent": vm.percent,
        },
        "disk": {
            "total_gb": round(disk.total / 1e9, 1),
            "free_gb": round(disk.free / 1e9, 1),
            "percent": disk.percent,
        },
    }
    try:
        battery = psutil.sensors_battery()
        if battery is not None:
            info["battery"] = {"percent": battery.percent, "plugged_in": battery.power_plugged}
    except Exception:
        pass
    return info


# ---------------------------------------------------------------------------
# Processes
# ---------------------------------------------------------------------------


def list_processes(query: str = "", limit: int = 30) -> list[dict]:
    require_enabled()
    psutil = _psutil()
    needle = (query or "").lower()
    rows: list[dict] = []
    for proc in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent"]):
        try:
            info = proc.info
            name = (info.get("name") or "").lower()
            if needle and needle not in name:
                continue
            rows.append(
                {
                    "pid": info["pid"],
                    "name": info.get("name") or "?",
                    "cpu_percent": round(info.get("cpu_percent") or 0.0, 1),
                    "memory_percent": round(info.get("memory_percent") or 0.0, 1),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    rows.sort(key=lambda r: r["memory_percent"], reverse=True)
    return rows[:limit]


def kill_process(pid: int) -> dict:
    require_enabled()
    psutil = _psutil()
    try:
        proc = psutil.Process(int(pid))
        name = proc.name()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
        return {"pid": pid, "name": name, "message": f"Stopped {name} (pid {pid})."}
    except psutil.NoSuchProcess:
        return {"error": f"No process with pid {pid}."}
    except psutil.AccessDenied:
        return {"error": f"Not allowed to stop pid {pid} (it belongs to another user or the system)."}


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------


def _no_clipboard() -> str:
    """Why the clipboard isn't reachable, in terms of the actual cause."""
    import importlib.util
    import shutil

    if os.name == "nt" or sys.platform == "darwin":
        return "The clipboard didn't respond. Try again, or copy it by hand."
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return (
            "There's no desktop session here, so there's no clipboard to read or write. "
            "Clipboard access needs a graphical session."
        )
    if not (shutil.which("xclip") or shutil.which("xsel") or shutil.which("wl-copy")):
        return (
            "No clipboard tool is installed. Add one: sudo apt install xclip "
            "(or xsel, or wl-clipboard on Wayland)."
        )
    if importlib.util.find_spec("pyperclip") is None:
        return (
            "Clipboard access needs the 'pyperclip' package: "
            "pip install -r requirements-device.txt"
        )
    return "The clipboard couldn't be reached on this system."


def read_clipboard() -> dict:
    require_enabled()
    return {"content": _clipboard_get()}


def write_clipboard(text: str) -> dict:
    require_enabled()
    _clipboard_set(text)
    return {"message": "Copied to the clipboard.", "length": len(text)}


def _clipboard_get() -> str:
    try:
        import pyperclip  # type: ignore

        return pyperclip.paste()
    except Exception:
        pass
    if sys.platform == "darwin":
        return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    if os.name == "nt":
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, **hidden_process(),
        ).stdout.rstrip("\n")
    raise DeviceUnavailable(_no_clipboard())


def _clipboard_set(text: str) -> None:
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return
    except Exception:
        pass
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text, text=True)
        return
    if os.name == "nt":
        subprocess.run(["clip"], input=text, text=True, **hidden_process())
        return
    raise DeviceUnavailable(_no_clipboard())


# ---------------------------------------------------------------------------
# Desktop notifications
# ---------------------------------------------------------------------------


def _plain(text: str) -> str:
    """One line of printable text: a notification has no use for control characters."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())


# PowerShell treats the typographic single quotes as quotes too, so all of them
# are doubled — the only escape a single-quoted PowerShell string has.
_PS_QUOTES = "'\u2018\u2019\u201a\u201b"


def ps_quote(text: str) -> str:
    """A PowerShell single-quoted string literal: nothing inside it is evaluated."""
    return "'" + "".join(ch * 2 if ch in _PS_QUOTES else ch for ch in text) + "'"


def desktop_notification(title: str, message: str = "") -> dict:
    """A native OS notification (not the in-app one). Best effort per platform.

    The title and message come from the model, so they are passed as data,
    never spliced into a script: as arguments to AppleScript, and as literal
    strings to PowerShell. A title like `$(Remove-Item ...)` or `" & do shell
    script "..."` is shown as written and runs nothing.
    """
    require_enabled()
    title = _plain(title or "AURA")[:200] or "AURA"
    message = _plain(message or "")[:500]
    try:
        if sys.platform == "darwin":
            subprocess.run(
                [
                    "osascript",
                    "-e", "on run argv",
                    "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                    "-e", "end run",
                    title,
                    message,
                ],
                check=False,
            )
        elif os.name == "nt":
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$n=New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                f"$n.ShowBalloonTip(5000,{ps_quote(title)},{ps_quote(message or ' ')},'Info');"
                "Start-Sleep -Seconds 6;$n.Dispose()"
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **hidden_process(),
            )
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", "--", title, message], check=False)
        else:
            raise DeviceUnavailable("No desktop notifier found (install libnotify's notify-send).")
    except DeviceUnavailable:
        raise
    except Exception as exc:
        return {"error": f"Couldn't post the notification: {exc}"}
    return {"message": "Posted a desktop notification.", "title": title}
