"""Running shell commands on this machine.

Two shapes:

* **Foreground** — run a command, wait up to a timeout, return its output. The
  common case: "what's the git status", "install this package".
* **Background** — start something long-lived (a dev server, a build), get a
  handle back immediately, then poll its output or stop it later. Handles live
  in an in-process registry; this is a single-process local runtime, so that's
  all the bookkeeping needed.

The denylist (policy.py) is enforced here too, as a floor: even a command that
was approved or runs at full trust is refused if it's on the denylist. Approval
itself happens upstream, in the agent loop.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from app.config import settings
from app.device import files, policy
from app.device.runtime import DeviceUnavailable, capabilities, require_enabled

_MAX_OUTPUT = 60_000  # chars kept per command / per background buffer


def _shell_argv(command: str) -> list[str]:
    """Wrap a command string for the platform's shell."""
    shell = capabilities()["shell"]
    kind, path = shell["kind"], shell["path"]
    if kind in ("pwsh", "powershell"):
        return [path, "-NoLogo", "-NonInteractive", "-Command", command]
    if kind == "cmd":
        return [path, "/c", command]
    return [path, "-lc", command]


def _workdir(workdir: str | None) -> str:
    """Where a command starts.

    An explicitly requested directory is fenced like any other path. The
    default is the user's home when that's inside the allowed roots, and
    otherwise the first allowed root — so a command starts somewhere the rest
    of the tools can also see.

    Worth being plain about: fencing the *starting* directory is not a sandbox.
    A shell command runs with this OS user's full authority and can reach
    anything that user can, whatever DEVICE_ALLOWED_ROOTS says — that limit
    binds the file tools, not the shell. What keeps the shell in check is the
    approval gate in front of it (and the denylist below it), which is why
    `run_command` sits in the highest risk class.
    """
    if workdir:
        return files.resolve(workdir, must_exist=True)

    home = os.path.abspath(os.path.expanduser("~"))
    roots = settings.device_allowed_root_list
    if any(files._within(home, root) for root in roots) and os.path.isdir(home):
        return home
    for root in roots:
        if os.path.isdir(root):
            return root
    return home


def _clip(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_OUTPUT:
        return text, False
    half = _MAX_OUTPUT // 2
    return text[:half] + "\n…[output truncated]…\n" + text[-half:], True


def _deny_floor(command: str) -> None:
    verdict = policy.classify(command)
    if not verdict.allowed:
        raise DeviceUnavailable(f"Refused: this command {verdict.reason}.")


# ---------------------------------------------------------------------------
# Foreground
# ---------------------------------------------------------------------------


def run(command: str, workdir: str | None = None, timeout: int | None = None) -> dict:
    require_enabled()
    command = (command or "").strip()
    if not command:
        return {"error": "No command given."}
    _deny_floor(command)

    cwd = _workdir(workdir)
    limit = timeout or settings.device_exec_timeout_seconds
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            _shell_argv(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=limit,
            # A fresh env is risky on Windows (SystemRoot etc.); inherit instead.
            env={**os.environ, "AURA_SHELL": "exec"},
        )
    except subprocess.TimeoutExpired as exc:
        out, _ = _clip((exc.stdout or "") + (exc.stderr or ""))
        return {
            "command": command,
            "timed_out": True,
            "timeout_seconds": limit,
            "output": out,
            "message": f"Killed after {limit}s. Use run_in_background for long-running work.",
        }
    except FileNotFoundError as exc:
        return {"command": command, "error": f"Couldn't start the shell: {exc}"}

    combined = (proc.stdout or "") + (
        ("\n" + proc.stderr) if proc.stderr and proc.returncode != 0 else ""
    )
    output, truncated = _clip(combined.strip())
    return {
        "command": command,
        "workdir": cwd,
        "exit_code": proc.returncode,
        "ok": proc.returncode == 0,
        "output": output or "(no output)",
        "truncated": truncated,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


@dataclass
class _Session:
    id: str
    command: str
    proc: subprocess.Popen
    started_at: float
    lines: deque = field(default_factory=lambda: deque(maxlen=4000))
    lock: threading.Lock = field(default_factory=threading.Lock)


_sessions: dict[str, _Session] = {}
_registry_lock = threading.Lock()


def _pump(session: _Session) -> None:
    """Read a background process's output into its ring buffer until it ends."""
    assert session.proc.stdout is not None
    for line in session.proc.stdout:
        with session.lock:
            session.lines.append(line.rstrip("\n"))
    session.proc.wait()


def run_in_background(command: str, workdir: str | None = None) -> dict:
    require_enabled()
    command = (command or "").strip()
    if not command:
        return {"error": "No command given."}
    _deny_floor(command)

    cwd = _workdir(workdir)
    proc = subprocess.Popen(
        _shell_argv(command),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={**os.environ, "AURA_SHELL": "exec"},
    )
    session = _Session(id=uuid.uuid4().hex[:12], command=command, proc=proc, started_at=time.time())
    with _registry_lock:
        _sessions[session.id] = session
    threading.Thread(target=_pump, args=(session,), daemon=True).start()
    return {
        "session_id": session.id,
        "command": command,
        "workdir": cwd,
        "status": "running",
        "message": f"Started in the background as {session.id}. Poll it with process_output.",
    }


def _tail(session: _Session, lines: int = 80) -> str:
    with session.lock:
        buf = list(session.lines)
    text = "\n".join(buf[-lines:])
    return _clip(text)[0]


def poll(session_id: str, lines: int = 80) -> dict:
    session = _sessions.get(session_id)
    if not session:
        return {"error": f"No background command {session_id}."}
    code = session.proc.poll()
    return {
        "session_id": session_id,
        "command": session.command,
        "status": "running" if code is None else "exited",
        "exit_code": code,
        "running_seconds": int(time.time() - session.started_at),
        "output": _tail(session, lines),
    }


def write_stdin(session_id: str, data: str) -> dict:
    session = _sessions.get(session_id)
    if not session:
        return {"error": f"No background command {session_id}."}
    if session.proc.poll() is not None:
        return {"error": "That command has already exited."}
    try:
        assert session.proc.stdin is not None
        session.proc.stdin.write(data if data.endswith("\n") else data + "\n")
        session.proc.stdin.flush()
    except (BrokenPipeError, OSError, AssertionError):
        return {"error": "Couldn't write to that command's input."}
    return {"session_id": session_id, "wrote": data}


def stop(session_id: str) -> dict:
    session = _sessions.get(session_id)
    if not session:
        return {"error": f"No background command {session_id}."}
    if session.proc.poll() is None:
        session.proc.terminate()
        try:
            session.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            session.proc.kill()
    return {"session_id": session_id, "status": "stopped", "exit_code": session.proc.poll()}


def list_sessions() -> list[dict]:
    out = []
    with _registry_lock:
        sessions = list(_sessions.values())
    for s in sessions:
        code = s.proc.poll()
        out.append(
            {
                "session_id": s.id,
                "command": s.command,
                "status": "running" if code is None else "exited",
                "exit_code": code,
                "running_seconds": int(time.time() - s.started_at),
            }
        )
    return out
