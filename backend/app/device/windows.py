"""Which window is in front, and which apps are open (spec 10 section 8.4).

`frontmost()` answers "what would a click land on right now?" for the screen floors. It
returns None whenever it cannot tell (no display server access, a missing helper, any
error) and the gate then fails closed. Nothing here raises.

Windows: ctypes (GetForegroundWindow and friends). macOS: osascript (System Events).
Linux on X11: xdotool. Wayland or no display: None.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from app.device import appnames
from app.device.runtime import hidden_process


@dataclass(frozen=True)
class Frontmost:
    app_id: str  # canonical (appnames.canonical)
    title: str
    pid: int
    name: str = ""  # as the system names it, for display


def _run(argv: list[str], timeout: float = 3.0) -> str | None:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, **hidden_process())
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _process_name(pid: int) -> str:
    try:
        import psutil  # type: ignore

        return psutil.Process(pid).name()
    except Exception:
        return ""


def _windows() -> Frontmost | None:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    return _describe_hwnd(user32, user32.GetForegroundWindow())


def _describe_hwnd(user32, hwnd) -> Frontmost | None:
    import ctypes
    from ctypes import wintypes

    if not hwnd:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = _process_name(pid.value)
    if not name:
        return None
    return Frontmost(appnames.canonical(name), buf.value, int(pid.value), name)


def _mac() -> Frontmost | None:
    script = (
        'tell application "System Events"\n'
        "set p to first application process whose frontmost is true\n"
        "set t to \"\"\n"
        "try\nset t to name of front window of p\nend try\n"
        'return (name of p) & "\\n" & (bundle identifier of p) & "\\n" & (unix id of p) & "\\n" & t\n'
        "end tell"
    )
    out = _run(["osascript", "-e", script])
    if not out:
        return None
    lines = out.rstrip("\n").split("\n")
    if len(lines) < 3 or not lines[2].strip().isdigit():
        return None
    name, bundle = lines[0].strip(), lines[1].strip()
    app_id = appnames.canonical(bundle if bundle and bundle != "missing value" else name)
    if not app_id:
        return None
    return Frontmost(app_id, lines[3] if len(lines) > 3 else "", int(lines[2]), name)


def _linux() -> Frontmost | None:
    if not os.environ.get("DISPLAY") or not shutil.which("xdotool"):
        return None
    win = (_run(["xdotool", "getactivewindow"]) or "").strip()
    if not win.isdigit():
        return None
    title = (_run(["xdotool", "getwindowname", win]) or "").strip()
    pid_text = (_run(["xdotool", "getwindowpid", win]) or "").strip()
    if not pid_text.isdigit():
        return None
    name = _process_name(int(pid_text))
    if not name:
        try:
            with open(f"/proc/{pid_text}/comm", encoding="utf-8") as fh:
                name = fh.read().strip()
        except OSError:
            return None
    return Frontmost(appnames.canonical(name), title, int(pid_text), name)


def _windows_at(x: int, y: int) -> Frontmost | None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    if not hwnd:
        return None
    return _describe_hwnd(user32, user32.GetAncestor(hwnd, 2) or hwnd)  # 2 = GA_ROOT: the top-level window


_JXA_AT = """
ObjC.import('CoreGraphics');
function run(argv) {
  var x = parseFloat(argv[0]), y = parseFloat(argv[1]);
  var list = ObjC.deepUnwrap(ObjC.castRefToObject(
    $.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements, 0)));
  for (var i = 0; i < list.length; i++) {
    var w = list[i], b = w.kCGWindowBounds;
    if (w.kCGWindowLayer !== 0 || !b) continue;
    if (x >= b.X && x < b.X + b.Width && y >= b.Y && y < b.Y + b.Height) {
      return w.kCGWindowOwnerPID + "\\n" + (w.kCGWindowOwnerName || "") + "\\n" + (w.kCGWindowName || "");
    }
  }
  return "";
}
"""


def _mac_at(x: int, y: int) -> Frontmost | None:
    out = _run(["osascript", "-l", "JavaScript", "-e", _JXA_AT, str(int(x)), str(int(y))], timeout=5.0)
    lines = (out or "").rstrip("\n").split("\n")
    if len(lines) < 2 or not lines[0].strip().isdigit():
        return None
    pid = int(lines[0])
    bundle = (_run(["osascript", "-e",
                    'tell application "System Events" to get bundle identifier of first process whose unix id is '
                    + str(pid)]) or "").strip()
    name = lines[1].strip()
    app_id = appnames.canonical(bundle if bundle and bundle != "missing value" else name)
    if not app_id:
        return None
    return Frontmost(app_id, lines[2] if len(lines) > 2 else "", pid, name)


def _linux_at(x: int, y: int) -> Frontmost | None:
    """The top-most window on screen that covers the point, from the window manager's stacking list
    (nothing is moved or touched to find it)."""
    if not os.environ.get("DISPLAY") or not shutil.which("xdotool") or not shutil.which("xprop"):
        return None
    listing = _run(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"]) or ""
    ids = [int(t, 16) for t in listing.replace(",", " ").split() if t.lower().startswith("0x")]
    for wid in reversed(ids):  # the list runs bottom to top
        geo = _run(["xdotool", "getwindowgeometry", "--shell", str(wid)]) or ""
        vals = dict(line.split("=", 1) for line in geo.splitlines() if "=" in line)
        try:
            left, top = int(vals["X"]), int(vals["Y"])
            right, bottom = left + int(vals["WIDTH"]), top + int(vals["HEIGHT"])
        except (KeyError, ValueError):
            continue
        if left <= x < right and top <= y < bottom:
            pid_text = (_run(["xdotool", "getwindowpid", str(wid)]) or "").strip()
            if not pid_text.isdigit():
                return None
            name = _process_name(int(pid_text))
            if not name:
                try:
                    with open(f"/proc/{pid_text}/comm", encoding="utf-8") as fh:
                        name = fh.read().strip()
                except OSError:
                    return None
            title = (_run(["xdotool", "getwindowname", str(wid)]) or "").strip()
            return Frontmost(appnames.canonical(name), title, int(pid_text), name)
    return None


def window_at(x: int, y: int) -> Frontmost | None:
    """The top-level window a click at screen point (x, y) would land on, or None when that cannot
    be told (the gate then refuses, F22). Nothing here raises."""
    try:
        if os.name == "nt":
            return _windows_at(x, y)
        if sys.platform == "darwin":
            return _mac_at(x, y)
        return _linux_at(x, y)
    except Exception:
        return None


def pointer_position() -> tuple[int, int] | None:
    """Where the mouse pointer is now, or None."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            pt = wintypes.POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):  # type: ignore[attr-defined]
                return int(pt.x), int(pt.y)
            return None
        try:
            import pyautogui  # type: ignore

            pos = pyautogui.position()
            return int(pos[0]), int(pos[1])
        except Exception:
            pass
        if shutil.which("xdotool") and os.environ.get("DISPLAY"):
            out = _run(["xdotool", "getmouselocation", "--shell"]) or ""
            vals = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            return int(vals["X"]), int(vals["Y"])
    except Exception:
        return None
    return None


def frontmost() -> Frontmost | None:
    try:
        if os.name == "nt":
            return _windows()
        if sys.platform == "darwin":
            return _mac()
        return _linux()
    except Exception:
        return None


def app_for_pid(pid: int) -> str | None:
    """The canonical app id of a process (needs psutil), or None."""
    name = _process_name(int(pid))
    return appnames.canonical(name) or None if name else None


def is_aura_process(pid: int) -> bool:
    """AURA's own process, or one it started (an ancestor of `pid` is this process)."""
    if pid in (os.getpid(),):
        return True
    try:
        import psutil  # type: ignore

        return any(p.pid == os.getpid() for p in psutil.Process(pid).parents())
    except Exception:
        return False


def running_apps(limit: int = 60) -> list[dict]:
    """Apps with a window open now, best effort: [{app_id, display_name}]."""
    names: list[str] = []
    try:
        if sys.platform == "darwin":
            out = _run(["osascript", "-e",
                        'tell application "System Events" to get name of every process whose background only is false'])
            names = [n.strip() for n in (out or "").split(",") if n.strip()]
        elif os.name == "nt":
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            pids: set[int] = set()
            proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def each(hwnd, _lparam):
                if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    pids.add(int(pid.value))
                return True

            user32.EnumWindows(proto(each), 0)
            names = [n for n in (_process_name(p) for p in pids) if n]
        elif shutil.which("wmctrl"):
            pids = set()
            for line in (_run(["wmctrl", "-lp"]) or "").splitlines():
                parts = line.split(None, 4)
                if len(parts) > 2 and parts[2].isdigit():
                    pids.add(int(parts[2]))
            names = [n for n in (_process_name(p) for p in pids) if n]
    except Exception:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for n in names:
        app_id = appnames.canonical(n)
        if app_id and app_id not in seen and not app_id.startswith("aura"):
            seen.add(app_id)
            out.append({"app_id": app_id, "display_name": os.path.splitext(n)[0] if n.lower().endswith(".exe") else n})
    return out[:limit]
