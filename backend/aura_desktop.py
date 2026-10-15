"""AURA AI for your computer — the desktop app's launcher.

Double-click AURA AI and this runs. It starts AURA on this computer — the API,
the background worker and the web app, in one process — opens it in your
browser, and keeps a small window open to reopen or stop it.

Everything AURA keeps lives in one folder, ~/.aura unless AURA_HOME says
otherwise:

    aura.env     your settings: model key, limits (made on first run)
    aura.db      everything AURA knows — chats, memory, tasks
    logs/        what happened, for when something didn't
    commands/    output of commands AURA runs in the background

That folder is fenced off from AURA's own file tools, as is the folder the app
is installed in, so the assistant can never rewrite its own settings or code.

From source, after `npm run build:desktop` in frontend/:

    python aura_desktop.py                 the app, with its window
    python aura_desktop.py --headless      no window, no browser
    python aura_desktop.py --smoke-test    start, check everything answers, exit
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "AURA AI"
DEFAULT_PORT = 8765
RELEASES_API = "https://api.github.com/repos/udaysk-lab/AURA-AI-/releases/latest"
RELEASES_PAGE = "https://github.com/udaysk-lab/AURA-AI-/releases/latest"

log = logging.getLogger("aura.desktop")


# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Where the files bundled with the app live."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def install_dir() -> Path:
    """The folder the app was installed to (the .app bundle on macOS)."""
    if is_frozen():
        exe = Path(sys.executable).resolve()
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
        return exe.parent
    return Path(__file__).resolve().parent.parent


def find_web_dir() -> Path | None:
    candidates = [
        os.environ.get("AURA_WEB_DIR", ""),
        str(resource_dir() / "web"),
        str(Path(__file__).resolve().parent.parent / "frontend" / "out"),
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / "index.html").is_file():
            return Path(candidate).resolve()
    return None


def default_home() -> Path:
    return Path(os.environ.get("AURA_HOME") or "~/.aura").expanduser()


# ---------------------------------------------------------------------------
# The settings file
# ---------------------------------------------------------------------------

SETTINGS_TEMPLATE = """\
# AURA AI — settings for this computer
#
# AURA reads this file when it starts. The model key and remote access can also
# be set inside the app (Settings → AI model, Settings → Use AURA from your
# phone); this file is where they are kept. Lines starting with # are notes.

# ---- The AI model ---------------------------------------------------------
# Without a key AURA runs in a limited offline demo mode.
# Get a Claude key at https://console.anthropic.com → API Keys.
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-5-5
#
# Or OpenAI, or any OpenAI-compatible endpoint (Ollama, LM Studio, Groq...):
# OPENAI_API_KEY=
# OPENAI_BASE_URL=http://localhost:11434/v1
# OPENAI_MODEL=llama3.1
#
# A cheaper model for background work, e.g. claude-haiku-5-5:
# BACKGROUND_MODEL=
#
# Most AURA will spend on the model in a day (US dollars):
# DAILY_SPEND_CAP_USD=5

# ---- What AURA may do on this computer -------------------------------------
# Folders the file tools may use (comma-separated). Blank = your user folder.
# DEVICE_ALLOWED_ROOTS=
# ask = every command waits for your approval (default) | allowlist | full
# DEVICE_EXEC_MODE=ask
# DEVICE_EXEC_ALLOWLIST=git status,git log,dir,ls
# Commands refused outright, even after approval:
# DEVICE_COMMAND_DENYLIST=shutdown,format,diskpart
# Set to false to stop AURA seeing the screen and using the mouse/keyboard:
# DEVICE_SCREEN_CONTROL=true

# ---- Using AURA from your phone ----------------------------------------------
# When true, AURA also listens on your network so a paired phone can reach it.
# Pair devices from Settings. Use it on networks you trust, or over Tailscale.
REMOTE_ACCESS=false
# AURA_PORT=8765

# ---- Generated on first run --------------------------------------------------
# Keep these private. Changing them signs you (and paired devices) out.
"""

GENERATED = ("SECRET_KEY", "TOKEN_ENCRYPTION_KEY", "AURA_LOCAL_LOGIN_KEY")


def _generate(name: str) -> str:
    if name == "TOKEN_ENCRYPTION_KEY":
        from cryptography.fernet import Fernet

        return Fernet.generate_key().decode()
    return secrets.token_urlsafe(36 if name == "SECRET_KEY" else 24)


def ensure_home(home: Path) -> Path:
    """Make the data folder and settings file; fill in any missing secrets."""
    from app import envfile  # no app.config import, so safe before setup

    home.mkdir(parents=True, exist_ok=True)
    for sub in ("logs", "commands"):
        (home / sub).mkdir(exist_ok=True)
    if os.name != "nt":
        os.chmod(home, 0o700)

    config = home / "aura.env"
    if not config.exists():
        config.write_text(SETTINGS_TEMPLATE, encoding="utf-8")
        if os.name != "nt":
            os.chmod(config, 0o600)

    values = envfile.read_values(config)
    missing = {name: _generate(name) for name in GENERATED if not values.get(name)}
    if missing:
        envfile.write_values(missing, config)
    return config


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def apply_environment(home: Path, config: Path, port: int, bind_host: str, web: Path | None) -> None:
    """Turn the settings file plus the desktop's fixed choices into the environment.

    Precedence: what the desktop app must decide (local mode, where its data
    lives) > the real environment > the settings file > desktop defaults.
    """
    from app import envfile

    for key, value in envfile.read_values(config).items():
        if value != "":
            os.environ.setdefault(key, value)

    base = f"http://127.0.0.1:{port}"
    forced = {
        "LOCAL_MODE": "true",
        "ENVIRONMENT": "local",
        "ALLOW_DEMO_LOGIN": "false",
        "AURA_HOME": str(home),
        "AURA_CONFIG_FILE": str(config),
        "AURA_PORT": str(port),
        "AURA_BIND_HOST": bind_host,
        "FRONTEND_URL": base,
        "BACKEND_URL": base,
        "CORS_ORIGINS": f"{base},http://localhost:{port}",
    }
    if web is not None:
        forced["AURA_WEB_DIR"] = str(web)
    os.environ.update(forced)

    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(home / 'aura.db').as_posix()}")
    os.environ.setdefault("DEBUG", "false")
    os.environ.setdefault("MAX_AGENT_STEPS", "16")

    # AURA's own data and its own code are off limits to its file tools.
    extra = [p for p in os.environ.get("DEVICE_EXTRA_PROTECTED_PATHS", "").split(",") if p.strip()]
    for path in (str(home), str(install_dir())):
        if path not in extra:
            extra.append(path)
    os.environ["DEVICE_EXTRA_PROTECTED_PATHS"] = ",".join(extra)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging(home: Path, console: bool) -> Path:
    # A windowed app has no console: stdout and stderr are None, and libraries
    # that print (or ask "is this a terminal?") would crash on them.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    log_file = home / "logs" / "aura.log"
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        root.addHandler(stream)
    return log_file


# ---------------------------------------------------------------------------
# The port, and whether AURA is already running
# ---------------------------------------------------------------------------


def running_aura(port: int) -> dict | None:
    """The /health of an AURA already answering on this port, if there is one."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as resp:
            data = json.loads(resp.read().decode())
        return data if data.get("app") == APP_NAME else None
    except Exception:
        return None


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def any_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


class Server:
    """Uvicorn on a thread, so the window can own the main thread (macOS needs it)."""

    def __init__(self, host: str, port: int):
        import uvicorn

        from app.main import app

        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_config=None,       # our handlers, set up above
            access_log=False,
            proxy_headers=False,   # the peer address is the real one; nothing fronts this
            loop="asyncio",
            http="h11",
            ws="none",
            lifespan="on",
            timeout_graceful_shutdown=5,
        )
        self.port = port
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="aura-server", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def wait_ready(self, timeout: float = 120.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.thread.is_alive():
                return False
            if self.server.started and running_aura(self.port):
                return True
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


# ---------------------------------------------------------------------------
# Small things the window offers
# ---------------------------------------------------------------------------


def open_folder(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        log.exception("Couldn't open %s", path)


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for piece in text.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def newer_release(current: str) -> str | None:
    """The latest release's version if it is newer than this one. Never raises."""
    if not _truthy(os.environ.get("AURA_UPDATE_CHECK", "true")):
        return None
    try:
        req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            tag = json.loads(resp.read().decode()).get("tag_name", "")
        if tag and _version_tuple(tag) > _version_tuple(current):
            return tag.lstrip("vV")
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


def run_window(app_url: str, open_aura, network_urls: list[str], home: Path, log_file: Path,
               version: str, stop) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title(APP_NAME)
    root.resizable(False, False)
    try:
        icon = tk.PhotoImage(file=str(resource_dir() / "assets" / "aura-icon.png"))
        root.iconphoto(True, icon)
    except Exception:
        pass

    frame = ttk.Frame(root, padding=(22, 18, 22, 16))
    frame.grid(sticky="nsew")

    ttk.Label(frame, text="AURA is running on this computer",
              font=("TkDefaultFont", 13, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(frame, text=app_url, foreground="#6d5dfc").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))
    ttk.Label(
        frame,
        text=("Your browser opened AURA. If it didn't, press Open AURA.\n"
              "Closing this window stops AURA."),
        justify="left",
    ).grid(row=2, column=0, columnspan=2, sticky="w")

    row = 3
    if network_urls:
        ttk.Label(
            frame,
            text="Also on your network (pair a phone from Settings):\n" + "\n".join(network_urls[:3]),
            justify="left", foreground="#555555",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        row += 1

    update_label = ttk.Label(frame, text="", foreground="#0a7f4f", cursor="hand2")
    update_label.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
    update_label.bind("<Button-1>", lambda _e: webbrowser.open(RELEASES_PAGE))
    row += 1

    buttons = ttk.Frame(frame)
    buttons.grid(row=row, column=0, columnspan=2, sticky="w", pady=(14, 6))
    ttk.Button(buttons, text="Open AURA", command=open_aura).grid(row=0, column=0, padx=(0, 8))

    def confirm_stop() -> None:
        if messagebox.askyesno(APP_NAME, "Stop AURA? Anything it's doing for you stops too."):
            root.destroy()

    ttk.Button(buttons, text="Stop AURA", command=confirm_stop).grid(row=0, column=1, padx=(0, 8))
    row += 1

    links = ttk.Frame(frame)
    links.grid(row=row, column=0, columnspan=2, sticky="w")
    for col, (label, target) in enumerate(
        (("Settings file", home), ("Logs", log_file.parent))
    ):
        link = ttk.Label(links, text=label, foreground="#6d5dfc", cursor="hand2")
        link.grid(row=0, column=col, padx=(0, 14))
        link.bind("<Button-1>", lambda _e, t=target: open_folder(t))
    ttk.Label(links, text=f"v{version}", foreground="#888888").grid(row=0, column=3, padx=(10, 0))

    def check_updates() -> None:
        latest = newer_release(version)
        if latest:
            root.after(0, lambda: update_label.config(
                text=f"AURA {latest} is available — click to download"))

    threading.Thread(target=check_updates, daemon=True).start()
    root.protocol("WM_DELETE_WINDOW", confirm_stop)
    root.mainloop()
    stop()


def show_error(message: str) -> None:
    """Tell someone who double-clicked an app, not just a log file."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def tk_available() -> bool:
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# Smoke test: what CI runs against the built app on every platform
# ---------------------------------------------------------------------------


def _request(url: str, method: str = "GET", body: dict | None = None,
             headers: dict | None = None, timeout: float = 60) -> tuple[int, dict, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")


def smoke_test(base: str, key: str) -> list[tuple[str, bool, str]]:
    import re

    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail[:300]))

    status, _, text = _request(f"{base}/health")
    check("health", status == 200 and json.loads(text).get("app") == APP_NAME, text)

    status, _, text = _request(f"{base}/api/auth/config")
    config = json.loads(text) if status == 200 else {}
    check("local mode on", config.get("local_mode") is True and config.get("device_control") is True, text)
    check("sign-in key required", config.get("local_login_key_required") is True, text)

    status, headers, page = _request(f"{base}/")
    check("web app index", status == 200 and "<html" in page.lower(), f"{status}")
    asset = re.search(r'/_next/static/[^"\']+\.js', page)
    if asset:
        status, headers, _ = _request(f"{base}{asset.group(0)}")
        ctype = headers.get("Content-Type") or headers.get("content-type") or ""
        check("web app script", status == 200 and "javascript" in ctype, f"{status} {ctype}")
    else:
        check("web app script", False, "no script found in index.html")
    status, _, page = _request(f"{base}/chat")
    check("web app page", status == 200 and "<html" in page.lower(), f"{status}")

    status, _, text = _request(f"{base}/api/auth/config", headers={"Host": "rebound.example"})
    check("refuses foreign Host", status == 421, f"{status}")

    status, _, text = _request(f"{base}/api/auth/local", method="POST", body={})
    check("sign-in refused without key", status == 403, f"{status}")
    status, _, text = _request(
        f"{base}/api/auth/register", method="POST",
        body={"email": "smoke-test@example.com", "password": "smoke-test-password", "name": "Smoke"},
    )
    check("no other accounts", status == 403, f"{status} {text[:120]}")
    status, _, text = _request(f"{base}/api/auth/local", method="POST", body={"key": key})
    token = json.loads(text).get("access_token", "") if status == 200 else ""
    check("owner sign-in", bool(token), f"{status} {text[:120]}")
    auth = {"Authorization": f"Bearer {token}"}

    status, _, text = _request(f"{base}/api/device", headers=auth)
    device = json.loads(text) if status == 200 else {}
    check("device control", device.get("enabled") is True, text[:200])

    status, _, text = _request(
        f"{base}/api/device/run", method="POST", headers=auth,
        body={"command": "echo aura-smoke-ok", "timeout": 60},
    )
    out = json.loads(text) if status == 200 else {}
    check("shell runs a command", "aura-smoke-ok" in (out.get("output") or ""), text[:300])

    status, _, text = _request(f"{base}/api/device/system", headers=auth)
    check("system info", status == 200, text[:200])

    status, _, text = _request(f"{base}/api/local/provider", headers=auth)
    check("model settings", status == 200 and json.loads(text).get("writable") is True, text[:200])

    status, _, text = _request(
        f"{base}/api/chat/stream", method="POST", headers=auth,
        body={"message": "What does my day look like?"}, timeout=120,
    )
    check("chat answers", status == 200 and '"type": "done"' in text, text[-300:])
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="aura", description=f"{APP_NAME} for your computer")
    parser.add_argument("--headless", action="store_true", help="no window and no browser")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--port", type=int, default=0, help=f"port (default {DEFAULT_PORT})")
    parser.add_argument("--data-dir", default="", help="where AURA keeps its files (default ~/.aura)")
    parser.add_argument("--smoke-test", action="store_true", help="start, check, and exit")
    parser.add_argument(
        "--smoke-report", default="",
        help="also write the smoke test's results to this JSON file (a windowed app has no console)",
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    # Ignore what macOS adds when an app is opened from Finder (-psn_0_1234).
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Relative to where it was launched from, before the app moves into its
    # data folder.
    if args.smoke_report:
        args.smoke_report = str(Path(args.smoke_report).resolve())
    from app.version import __version__

    if args.version:
        print(__version__)
        return 0

    smoke = args.smoke_test
    home = Path(args.data_dir).expanduser() if args.data_dir else (
        Path(tempfile.mkdtemp(prefix="aura-smoke-")) if smoke else default_home()
    )
    try:
        config = ensure_home(home)
    except Exception as exc:
        show_error(f"AURA couldn't create its data folder at {home}:\n\n{exc}")
        return 1

    from app import envfile

    file_values = envfile.read_values(config)
    if smoke:
        # The checks exercise the agent path; they must not spend real money
        # because a key happens to be in this machine's environment.
        os.environ["LLM_PROVIDER"] = "mock"
    headless = args.headless or smoke or not tk_available()
    log_file = setup_logging(home, console=headless or not is_frozen())
    log.info("%s %s starting (data folder %s)", APP_NAME, __version__, home)

    remote = _truthy(os.environ.get("REMOTE_ACCESS") or file_values.get("REMOTE_ACCESS"))
    bind_host = "0.0.0.0" if remote and not smoke else "127.0.0.1"

    wanted = args.port or int(os.environ.get("AURA_PORT") or file_values.get("AURA_PORT") or DEFAULT_PORT)
    if smoke:
        port = any_free_port()
    else:
        existing = running_aura(wanted)
        if existing:
            # Already running (a second double-click): just bring it up.
            key = file_values.get("AURA_LOCAL_LOGIN_KEY", "")
            log.info("AURA is already running on port %s; opening it", wanted)
            if not args.headless and not args.no_browser:
                webbrowser.open(f"http://127.0.0.1:{wanted}/login#key={key}")
            return 0
        port = wanted if port_free(bind_host, wanted) else any_free_port()
        if port != wanted:
            log.warning("Port %s is taken by another program; using %s", wanted, port)

    web = find_web_dir()
    if web is None:
        log.warning("No built web app found; only the API will be served. "
                    "Run `npm run build:desktop` in frontend/ first.")
    apply_environment(home, config, port, bind_host, web)
    os.chdir(home)

    try:
        server = Server(bind_host, port)
    except Exception as exc:
        log.exception("AURA failed to load")
        show_error(f"AURA couldn't start:\n\n{exc}\n\nDetails are in {log_file}")
        return 1
    server.start()
    if not server.wait_ready():
        log.error("AURA didn't come up; see the log above")
        if not headless:
            show_error(f"AURA couldn't start. Details are in:\n\n{log_file}")
        if smoke and args.smoke_report:
            Path(args.smoke_report).write_text(
                json.dumps({"version": __version__, "failed": ["server started"], "log": str(log_file)}),
                encoding="utf-8",
            )
        server.stop()
        return 1

    app_url = f"http://127.0.0.1:{port}"
    key = os.environ.get("AURA_LOCAL_LOGIN_KEY", "")
    log.info("AURA is ready at %s", app_url)

    if smoke:
        try:
            results = smoke_test(app_url, key)
        except Exception as exc:  # a crash in the checks is a failed check
            log.exception("Smoke test crashed")
            results = [("smoke test ran", False, repr(exc))]
        width = max(len(name) for name, _, _ in results)
        for name, ok, detail in results:
            print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {'' if ok else detail}")
        server.stop()
        failed = [name for name, ok, _ in results if not ok]
        print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
        if args.smoke_report:
            report = {
                "version": __version__,
                "platform": sys.platform,
                "passed": len(results) - len(failed),
                "failed": failed,
                "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results],
                "log": str(log_file),
            }
            Path(args.smoke_report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 1 if failed else 0

    def open_aura() -> None:
        webbrowser.open(f"{app_url}/login#key={key}")

    if not (args.headless or args.no_browser):
        open_aura()

    network_urls: list[str] = []
    if bind_host != "127.0.0.1":
        from app.api.remote import pairing_urls

        network_urls = pairing_urls()

    if headless:
        print(f"\n  {APP_NAME} {__version__} is running at {app_url}")
        print(f"  Sign in:  {app_url}/login#key={key}")
        for url in network_urls:
            print(f"  On your network: {url}")
        print("  Press Ctrl+C to stop.\n")
        try:
            while server.thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        server.stop()
        return 0

    run_window(app_url, open_aura, network_urls, home, log_file, __version__, server.stop)
    log.info("AURA stopped")
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
