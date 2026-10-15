"""What this machine is, and what AURA can do on it.

One place answers "are we allowed to touch the device, and with what?" so every
tool, the API, and the system prompt agree. Capability probing is cheap and
cached: it reports which optional pieces (screen control, process inspection,
clipboard) are actually installed, so the rest of the system degrades honestly
instead of raising deep in a tool call.
"""

from __future__ import annotations

import functools
import os
import platform
import shutil
import sys

from app.config import settings


class DeviceUnavailable(RuntimeError):
    """Raised when a device action is attempted but can't be served.

    Carries a plain, user-facing reason. Tools turn it into {"error": ...}
    rather than letting it crash the agent loop.
    """


def require_enabled() -> None:
    """Gate every device action. Off on any non-local deployment."""
    if not settings.device_control_enabled:
        raise DeviceUnavailable(
            "Device control is off. It's a local-only capability; set LOCAL_MODE=true "
            "(or DEVICE_CONTROL=on) and restart to let AURA operate this computer."
        )


def hidden_process() -> dict:
    """Keyword arguments that keep a helper process from flashing a window.

    The desktop app has no console of its own, so on Windows every console
    program it starts — PowerShell, cmd, taskkill, clip — would otherwise pop
    up a black window for as long as it runs. A GUI program started *through*
    one of them (notepad, a browser) still shows its own window as usual.
    """
    if os.name == "nt":
        import subprocess

        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def data_dir(*parts: str) -> str:
    """A folder inside AURA's own data folder (~/.aura), created on demand."""
    path = os.path.join(settings.aura_home_path, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def _has_module(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _default_shell() -> dict:
    """The shell AURA will run commands through, chosen per platform.

    Windows prefers PowerShell 7 (`pwsh`), then Windows PowerShell, then cmd.
    Everything else prefers the user's $SHELL when it's a real POSIX shell,
    otherwise bash, otherwise sh.
    """
    if os.name == "nt":
        for name, exe in (("pwsh", "pwsh"), ("powershell", "powershell")):
            found = shutil.which(exe)
            if found:
                return {"kind": name, "path": found}
        return {"kind": "cmd", "path": shutil.which("cmd") or "cmd.exe"}

    env_shell = os.environ.get("SHELL", "")
    # fish can't run the POSIX one-liners we build, so skip past it.
    if env_shell and "fish" not in env_shell and os.path.exists(env_shell):
        return {"kind": os.path.basename(env_shell), "path": env_shell}
    for exe in ("bash", "sh"):
        found = shutil.which(exe)
        if found:
            return {"kind": exe, "path": found}
    return {"kind": "sh", "path": "/bin/sh"}


def _clipboard_ready() -> bool:
    """Is there actually a clipboard we can reach?

    Windows and macOS ship one (clip / pbcopy), so the package is a nicety
    there. On Linux the package alone proves nothing: pyperclip shells out to
    xclip or xsel, and on a headless box neither exists — which is how a probe
    that only checked for the import came to report a clipboard that then
    failed on first use.
    """
    if os.name == "nt" or sys.platform == "darwin":
        return True
    if not _has_display():
        return False
    return bool(shutil.which("xclip") or shutil.which("xsel") or shutil.which("wl-copy"))


def _has_display() -> bool:
    """Is there a desktop session screen control could drive?"""
    if os.name == "nt" or sys.platform == "darwin":
        return True  # assume an interactive session; pyautogui import is the real test
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


@functools.lru_cache(maxsize=1)
def _probe() -> dict:
    system = platform.system()  # Windows | Darwin | Linux
    os_label = {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(system, system)

    screen_ready = (
        settings.device_screen_control and _has_display() and _has_module("pyautogui")
    )

    return {
        "os": os_label,
        "platform": platform.platform(),
        "release": platform.release(),
        "arch": platform.machine(),
        "hostname": platform.node(),
        "python": platform.python_version(),
        "user": _current_user(),
        "home": os.path.abspath(os.path.expanduser("~")),
        "cwd": os.getcwd(),
        "shell": _default_shell(),
        # Optional feature availability
        "shell_control": True,
        "file_control": True,
        "process_control": _has_module("psutil"),
        "screen_control": screen_ready,
        "clipboard": _clipboard_ready(),
        "has_display": _has_display(),
        # Optional packages, so the UI and /setup can say what to install
        "packages": {
            "psutil": _has_module("psutil"),
            "pyautogui": _has_module("pyautogui"),
            "pyperclip": _has_module("pyperclip"),
        },
    }


def _current_user() -> str:
    try:
        import getpass

        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


def capabilities(include_policy: bool = False) -> dict:
    """A snapshot of the machine and what AURA may do on it.

    `enabled` is the master switch; everything else describes the surface. When
    `include_policy` is set, also reports the allowed roots and exec mode, for
    the Device settings screen.
    """
    data = {"enabled": settings.device_control_enabled, **_probe()}
    if not data["enabled"]:
        # Don't advertise a surface the agent can't use.
        for key in ("shell_control", "file_control", "process_control", "screen_control"):
            data[key] = False
    if include_policy:
        data["policy"] = {
            "allowed_roots": settings.device_allowed_root_list,
            "protected_paths": settings.device_protected_path_list,
            "exec_mode": settings.device_exec_mode,
            "exec_allowlist": settings.device_exec_allow_list,
            "denylist_active": bool(settings.device_command_deny_list),
            "exec_timeout_seconds": settings.device_exec_timeout_seconds,
            "max_read_bytes": settings.device_max_read_bytes,
        }
    return data


def reset_cache() -> None:
    """Forget the probe. Used by tests that toggle optional packages."""
    _probe.cache_clear()


def os_facts_line() -> str:
    """One line for the system prompt: who and where the agent is operating."""
    p = _probe()
    return (
        f"{p['os']} ({p['arch']}), host {p['hostname']}, user {p['user']}, "
        f"shell {p['shell']['kind']}, home {p['home']}"
    )
