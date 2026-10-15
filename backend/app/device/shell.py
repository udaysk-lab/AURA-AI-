"""Running shell commands on this machine.

Two shapes:

* **Foreground** — run a command, wait up to a timeout, return its output. The
  common case: "what's the git status", "install this package".
* **Background** — start something long-lived (a dev server, a build), get a
  handle back immediately, then poll its output or stop it later. Its output
  goes to a file in ~/.aura/commands, next to a small record of what it is, so
  AURA still knows about it — can show its output and stop it — after AURA
  itself restarts. (Typing into it needs the live pipe, so that one thing
  doesn't survive a restart.)

The denylist (policy.py) is enforced here too, as a floor: even a command that
was approved or runs at full trust is refused if it's on the denylist. Approval
itself happens upstream, in the agent loop.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass

from app.config import settings
from app.device import files, policy
from app.device.runtime import (
    DeviceUnavailable,
    capabilities,
    data_dir,
    hidden_process,
    require_enabled,
)

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
    return home if os.path.isdir(home) else os.getcwd()


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
            # Nothing to type into a foreground command: one that waits for
            # input gets end-of-file at once rather than hanging to the timeout.
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
            # A fresh env is risky on Windows (SystemRoot etc.); inherit instead.
            env={**os.environ, "AURA_SHELL": "exec"},
            **hidden_process(),
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

# How much of a background command's output a poll reads back, at most.
_TAIL_BYTES = 64_000
# Records of finished commands older than this are tidied away.
_KEEP_SECONDS = 7 * 24 * 3600


@dataclass
class _Record:
    """What AURA remembers about a background command, on disk."""

    id: str
    command: str
    workdir: str
    pid: int
    started_at: float
    log_path: str
    # The OS's own start time for the process, when psutil can tell. Lets a
    # restarted AURA tell its command from an unrelated program that has since
    # been given the same process id.
    create_time: float | None = None
    exit_code: int | None = None
    ended_at: float | None = None
    stopped: bool = False


_live: dict[str, subprocess.Popen] = {}  # only commands this process started
_lock = threading.Lock()


def _commands_dir() -> str:
    return data_dir("commands")


def _record_path(session_id: str) -> str:
    return os.path.join(_commands_dir(), f"{session_id}.json")


def _save(record: _Record) -> None:
    path = _record_path(record.id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(asdict(record), handle)
    os.replace(tmp, path)


def _load(session_id: str) -> _Record | None:
    if not session_id or not all(c.isalnum() for c in session_id):
        return None
    try:
        with open(_record_path(session_id), encoding="utf-8") as handle:
            return _Record(**json.load(handle))
    except (OSError, ValueError, TypeError):
        return None


def _process_start_time(pid: int) -> float | None:
    try:
        import psutil  # type: ignore

        return psutil.Process(pid).create_time()
    except Exception:
        return None


def _alive(record: _Record) -> bool:
    """Is this command still running — this command, not a pid reused since?"""
    proc = _live.get(record.id)
    if proc is not None:
        return proc.poll() is None
    if record.exit_code is not None or record.stopped:
        return False
    try:
        import psutil  # type: ignore

        process = psutil.Process(record.pid)
        if record.create_time is not None and abs(process.create_time() - record.create_time) > 1:
            return False  # the pid belongs to something else now
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except ImportError:
        pass
    except Exception:
        return False
    if os.name == "nt":
        return False  # can't tell safely without psutil
    try:
        os.kill(record.pid, 0)
    except OSError:
        return False
    return True


def _exit_code(record: _Record) -> int | None:
    proc = _live.get(record.id)
    if proc is not None:
        return proc.poll()
    return record.exit_code


def _watch(record: _Record, proc: subprocess.Popen) -> None:
    """Note the exit code on disk when the command ends, for a later AURA."""
    code = proc.wait()
    with _lock:
        latest = _load(record.id) or record
        latest.exit_code = code
        latest.ended_at = time.time()
        _save(latest)


def run_in_background(command: str, workdir: str | None = None) -> dict:
    require_enabled()
    command = (command or "").strip()
    if not command:
        return {"error": "No command given."}
    _deny_floor(command)

    cwd = _workdir(workdir)
    session_id = uuid.uuid4().hex[:12]
    log_path = os.path.join(_commands_dir(), f"{session_id}.log")
    extra: dict = dict(hidden_process())
    if os.name == "nt":
        extra["creationflags"] = extra.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # Its own process group, so stopping it stops what it started too
        # (a dev server's child processes), not just the shell.
        extra["start_new_session"] = True

    with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
        proc = subprocess.Popen(
            _shell_argv(command),
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "AURA_SHELL": "exec", "PYTHONUNBUFFERED": "1"},
            **extra,
        )

    record = _Record(
        id=session_id,
        command=command,
        workdir=cwd,
        pid=proc.pid,
        started_at=time.time(),
        log_path=log_path,
        create_time=_process_start_time(proc.pid),
    )
    with _lock:
        _live[session_id] = proc
        _save(record)
    threading.Thread(target=_watch, args=(record, proc), daemon=True).start()
    _tidy()
    return {
        "session_id": session_id,
        "command": command,
        "workdir": cwd,
        "status": "running",
        "message": f"Started in the background as {session_id}. Poll it with process_output.",
    }


def _tail(record: _Record, lines: int = 80) -> str:
    try:
        with open(record.log_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _TAIL_BYTES))
            data = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    text = "\n".join(data.splitlines()[-max(1, lines):])
    return _clip(text)[0]


def _status(record: _Record) -> tuple[str, int | None]:
    if _alive(record):
        return "running", None
    code = _exit_code(record)
    if record.stopped:
        return "stopped", code
    return "exited", code


def poll(session_id: str, lines: int = 80) -> dict:
    record = _load(session_id)
    if not record:
        return {"error": f"No background command {session_id}."}
    status, code = _status(record)
    end = record.ended_at if status != "running" and record.ended_at else time.time()
    return {
        "session_id": session_id,
        "command": record.command,
        "status": status,
        "exit_code": code,
        "running_seconds": int(end - record.started_at),
        "output": _tail(record, lines),
    }


def write_stdin(session_id: str, data: str) -> dict:
    record = _load(session_id)
    if not record:
        return {"error": f"No background command {session_id}."}
    proc = _live.get(session_id)
    if proc is None:
        return {
            "error": (
                "AURA has restarted since this command started, so it can no longer type "
                "into it. Its output can still be read, and it can still be stopped."
            )
        }
    if proc.poll() is not None:
        return {"error": "That command has already exited."}
    try:
        assert proc.stdin is not None
        proc.stdin.write(data if data.endswith("\n") else data + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError, AssertionError, ValueError):
        return {"error": "Couldn't write to that command's input."}
    return {"session_id": session_id, "wrote": data}


def _kill_tree(record: _Record) -> None:
    """Stop the command and everything it started."""
    proc = _live.get(record.id)
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(record.pid), "/T", "/F"],
            capture_output=True, check=False, **hidden_process(),
        )
    else:
        try:
            os.killpg(record.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.time() + 5
        while time.time() < deadline and _alive(record):
            time.sleep(0.1)
        if _alive(record):
            try:
                os.killpg(record.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    if proc is not None:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def stop(session_id: str) -> dict:
    record = _load(session_id)
    if not record:
        return {"error": f"No background command {session_id}."}
    if _alive(record):
        if session_id not in _live and record.create_time is None:
            # Started before a restart, and without psutil there's no way to be
            # sure the pid is still ours. Killing a stranger's process would be
            # worse than asking.
            return {
                "error": (
                    "AURA restarted since starting this command and can't confirm the "
                    f"process (pid {record.pid}) is still the same one. Stop it from the "
                    "process list instead."
                )
            }
        _kill_tree(record)
    with _lock:
        latest = _load(session_id) or record
        latest.stopped = True
        if latest.exit_code is None:
            latest.exit_code = _exit_code(latest)
        latest.ended_at = latest.ended_at or time.time()
        _save(latest)
    return {"session_id": session_id, "status": "stopped", "exit_code": latest.exit_code}


def _records() -> list[_Record]:
    try:
        names = os.listdir(_commands_dir())
    except OSError:
        return []
    out = []
    for name in names:
        if name.endswith(".json"):
            record = _load(name[:-5])
            if record:
                out.append(record)
    return sorted(out, key=lambda r: r.started_at, reverse=True)


def _tidy() -> None:
    """Forget finished commands older than a week (record and output)."""
    cutoff = time.time() - _KEEP_SECONDS
    for record in _records():
        if (record.ended_at or record.started_at) < cutoff and not _alive(record):
            for path in (_record_path(record.id), record.log_path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            _live.pop(record.id, None)


def list_sessions() -> list[dict]:
    out = []
    for record in _records()[:50]:
        status, code = _status(record)
        end = record.ended_at if status != "running" and record.ended_at else time.time()
        out.append(
            {
                "session_id": record.id,
                "command": record.command,
                "status": status,
                "exit_code": code,
                "running_seconds": int(end - record.started_at),
            }
        )
    return out
