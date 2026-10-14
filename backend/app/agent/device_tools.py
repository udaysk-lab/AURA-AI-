"""Device-control tools.

These are the hands AURA grows when it runs on your own machine: a shell, the
filesystem, processes, apps, the screen, the clipboard. They register into the
same `REGISTRY` as every other tool (importing this module is enough), so the
coordinator dispatches them, the autonomy gate approves them, and the skill log
records them with no special-casing.

Every handler is wrapped so a `DeviceUnavailable` — control switched off, an
optional package missing, a path outside the fence — comes back as a tidy
`{"error": ...}` the agent can read and explain, instead of crashing the turn.
Risk classification (what needs your approval) lives in `agent/autonomy.py`.
"""

from __future__ import annotations

import functools
import logging

from app.agent.tools import ToolContext, _log, tool
from app.device import apps, files, screen, shell, system
from app.device.runtime import DeviceUnavailable

log = logging.getLogger("aura.device")


def _safe(fn):
    """Turn DeviceUnavailable (and any stray error) into an {"error": ...} dict."""

    @functools.wraps(fn)
    def wrapper(ctx: ToolContext, args: dict):
        try:
            return fn(ctx, args)
        except DeviceUnavailable as exc:
            return {"error": str(exc)}
        except Exception as exc:  # a tool must never take the loop down
            log.exception("Device tool %s failed", fn.__name__)
            return {"error": f"{fn.__name__} failed: {exc}"}

    return wrapper


# ---------------------------------------------------------------------------
# Shell & processes
# ---------------------------------------------------------------------------


@tool(
    "run_command",
    "Run a shell command on this computer and return its output. Use for anything the "
    "machine can do from a terminal: git, package managers, scripts, file wrangling. "
    "Runs in the user's shell; prefer this over guessing. Long-running services go to "
    "run_in_background instead.",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The exact shell command to run."},
            "workdir": {"type": "string", "description": "Directory to run in (optional)."},
            "timeout": {"type": "integer", "description": "Seconds before it's killed (optional)."},
        },
        "required": ["command"],
    },
    destructive=True,
    preview=lambda a: f"Run on this computer:\n\n    {a.get('command', '')}"
    + (f"\n\n(in {a['workdir']})" if a.get("workdir") else ""),
)
@_safe
def _run_command(ctx: ToolContext, args: dict) -> dict:
    result = shell.run(args["command"], args.get("workdir"), args.get("timeout"))
    _log(ctx, "run_command", args["command"][:200],
         {"exit_code": result.get("exit_code")},
         status="success" if result.get("ok") else "error")
    return result


@tool(
    "run_in_background",
    "Start a long-running command (a dev server, a build, a watch) and get a handle back "
    "immediately. Poll it with process_output, stop it with stop_process.",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "workdir": {"type": "string"},
        },
        "required": ["command"],
    },
    destructive=True,
    preview=lambda a: f"Start in the background on this computer:\n\n    {a.get('command', '')}",
)
@_safe
def _run_in_background(ctx: ToolContext, args: dict) -> dict:
    result = shell.run_in_background(args["command"], args.get("workdir"))
    _log(ctx, "run_in_background", args["command"][:200], {"session": result.get("session_id")})
    return result


@tool(
    "process_output",
    "Read the latest output of a background command started with run_in_background, and "
    "whether it's still running.",
    {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "lines": {"type": "integer", "minimum": 1, "maximum": 400},
        },
        "required": ["session_id"],
    },
)
@_safe
def _process_output(ctx: ToolContext, args: dict) -> dict:
    return shell.poll(args["session_id"], int(args.get("lines") or 80))


@tool(
    "send_process_input",
    "Type a line into a running background command's input (answer a prompt, send 'y').",
    {
        "type": "object",
        "properties": {"session_id": {"type": "string"}, "data": {"type": "string"}},
        "required": ["session_id", "data"],
    },
    destructive=True,
    preview=lambda a: f"Send to background command {a.get('session_id')}: {a.get('data')!r}",
)
@_safe
def _send_process_input(ctx: ToolContext, args: dict) -> dict:
    return shell.write_stdin(args["session_id"], args["data"])


@tool(
    "stop_process",
    "Stop a background command started with run_in_background.",
    {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]},
    destructive=True,
    preview=lambda a: f"Stop background command {a.get('session_id')}",
)
@_safe
def _stop_process(ctx: ToolContext, args: dict) -> dict:
    return shell.stop(args["session_id"])


@tool(
    "list_background_commands",
    "List the background commands AURA has running this session.",
)
@_safe
def _list_background(ctx: ToolContext, args: dict) -> list:
    return shell.list_sessions()


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


@tool(
    "read_file",
    "Read a text file from this computer (within the folders AURA may access).",
    {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
)
@_safe
def _read_file(ctx: ToolContext, args: dict) -> dict:
    return files.read_text(args["path"])


@tool(
    "write_file",
    "Write a text file on this computer, creating folders as needed. Overwrites unless "
    "append is set.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "append": {"type": "boolean"},
        },
        "required": ["path", "content"],
    },
    destructive=True,
    preview=lambda a: f"Write {len(a.get('content', ''))} char(s) to {a.get('path')}"
    + (" (append)" if a.get("append") else " (overwrite)"),
)
@_safe
def _write_file(ctx: ToolContext, args: dict) -> dict:
    result = files.write_text(args["path"], args["content"], append=bool(args.get("append")))
    _log(ctx, "write_file", args["path"])
    return result


@tool(
    "edit_file",
    "Replace exact text in a file. Read it first and copy the text to find precisely.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "find": {"type": "string"},
            "replace": {"type": "string"},
            "count": {"type": "integer", "description": "0 = every occurrence."},
        },
        "required": ["path", "find", "replace"],
    },
    destructive=True,
    preview=lambda a: f"Edit {a.get('path')}: replace {a.get('find', '')[:60]!r} "
    f"with {a.get('replace', '')[:60]!r}",
)
@_safe
def _edit_file(ctx: ToolContext, args: dict) -> dict:
    result = files.edit_text(args["path"], args["find"], args["replace"], count=int(args.get("count") or 0))
    _log(ctx, "edit_file", args["path"])
    return result


@tool(
    "list_directory",
    "List the files and folders in a directory on this computer.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "show_hidden": {"type": "boolean"},
        },
    },
)
@_safe
def _list_directory(ctx: ToolContext, args: dict) -> dict:
    return files.list_directory(args.get("path") or "~", bool(args.get("show_hidden")))


@tool(
    "search_files",
    "Find files by name, or search inside their text, under a folder on this computer.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "root": {"type": "string"},
            "in_content": {"type": "boolean", "description": "Search file contents, not names."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["query"],
    },
)
@_safe
def _search_files(ctx: ToolContext, args: dict) -> list:
    return files.search_files(
        args["query"], args.get("root") or "~",
        in_content=bool(args.get("in_content")), limit=int(args.get("limit") or 50),
    )


@tool(
    "move_file",
    "Move or rename a file or folder on this computer.",
    {
        "type": "object",
        "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
        "required": ["source", "destination"],
    },
    destructive=True,
    preview=lambda a: f"Move {a.get('source')} → {a.get('destination')}",
)
@_safe
def _move_file(ctx: ToolContext, args: dict) -> dict:
    result = files.move_path(args["source"], args["destination"])
    _log(ctx, "move_file", f"{args['source']} -> {args['destination']}")
    return result


@tool(
    "delete_file",
    "Permanently delete a file or folder on this computer. There is no undo.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    destructive=True,
    preview=lambda a: f"Permanently delete {a.get('path')} (no undo)",
)
@_safe
def _delete_file(ctx: ToolContext, args: dict) -> dict:
    result = files.delete_path(args["path"])
    _log(ctx, "delete_file", args["path"], status="success")
    return result


@tool(
    "make_directory",
    "Create a folder (and any parent folders) on this computer.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    destructive=True,
    preview=lambda a: f"Create folder {a.get('path')}",
)
@_safe
def _make_directory(ctx: ToolContext, args: dict) -> dict:
    return files.make_directory(args["path"])


# ---------------------------------------------------------------------------
# Apps, system, clipboard
# ---------------------------------------------------------------------------


@tool(
    "open_app",
    "Open an application, a file, or a URL on this computer.",
    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    destructive=True,
    preview=lambda a: f"Open {a.get('name')}",
)
@_safe
def _open_app(ctx: ToolContext, args: dict) -> dict:
    result = apps.open_app(args["name"])
    _log(ctx, "open_app", args["name"])
    return result


@tool(
    "close_app",
    "Close an application on this computer.",
    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    destructive=True,
    preview=lambda a: f"Close {a.get('name')}",
)
@_safe
def _close_app(ctx: ToolContext, args: dict) -> dict:
    return apps.close_app(args["name"])


@tool(
    "list_applications",
    "List the applications installed on this computer.",
)
@_safe
def _list_applications(ctx: ToolContext, args: dict) -> list:
    return apps.list_applications()


@tool(
    "system_info",
    "Report this computer's CPU, memory, disk and battery state.",
)
@_safe
def _system_info(ctx: ToolContext, args: dict) -> dict:
    return system.system_info()


@tool(
    "list_processes",
    "List the processes running on this computer, heaviest first.",
    {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
    },
)
@_safe
def _list_processes(ctx: ToolContext, args: dict) -> list:
    return system.list_processes(args.get("query") or "", int(args.get("limit") or 30))


@tool(
    "kill_process",
    "Stop a running process by its PID.",
    {"type": "object", "properties": {"pid": {"type": "integer"}}, "required": ["pid"]},
    destructive=True,
    preview=lambda a: f"Stop process with PID {a.get('pid')}",
)
@_safe
def _kill_process(ctx: ToolContext, args: dict) -> dict:
    result = system.kill_process(int(args["pid"]))
    _log(ctx, "kill_process", str(args.get("pid")))
    return result


@tool(
    "read_clipboard",
    "Read the current text on this computer's clipboard.",
)
@_safe
def _read_clipboard(ctx: ToolContext, args: dict) -> dict:
    return system.read_clipboard()


@tool(
    "set_clipboard",
    "Put text on this computer's clipboard.",
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    destructive=True,
    preview=lambda a: f"Copy to clipboard: {a.get('text', '')[:80]!r}",
)
@_safe
def _set_clipboard(ctx: ToolContext, args: dict) -> dict:
    return system.write_clipboard(args["text"])


@tool(
    "desktop_notify",
    "Show a native desktop notification on this computer.",
    {
        "type": "object",
        "properties": {"title": {"type": "string"}, "message": {"type": "string"}},
        "required": ["title"],
    },
)
@_safe
def _desktop_notify(ctx: ToolContext, args: dict) -> dict:
    return system.desktop_notification(args["title"], args.get("message") or "")


# ---------------------------------------------------------------------------
# Screen, mouse, keyboard
# ---------------------------------------------------------------------------


@tool(
    "take_screenshot",
    "Capture the screen and save it, returning the image path so you can view what's on "
    "screen. Use before clicking so you know where things are.",
)
@_safe
def _take_screenshot(ctx: ToolContext, args: dict) -> dict:
    result = screen.screenshot()
    _log(ctx, "take_screenshot", result.get("path", ""))
    return result


@tool(
    "mouse_click",
    "Click the mouse on this computer, optionally at a screen pixel. Take a screenshot "
    "first to find coordinates.",
    {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "clicks": {"type": "integer", "minimum": 1, "maximum": 3},
        },
    },
    destructive=True,
    preview=lambda a: f"{(a.get('button') or 'left').capitalize()}-click"
    + (f" at ({a['x']}, {a['y']})" if a.get("x") is not None and a.get("y") is not None else "")
    + (f" ×{a['clicks']}" if a.get("clicks") else ""),
)
@_safe
def _mouse_click(ctx: ToolContext, args: dict) -> dict:
    return screen.click(args.get("x"), args.get("y"), args.get("button") or "left", int(args.get("clicks") or 1))


@tool(
    "type_text",
    "Type text on this computer's keyboard, into whatever has focus.",
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    destructive=True,
    preview=lambda a: f"Type: {a.get('text', '')[:120]!r}",
)
@_safe
def _type_text(ctx: ToolContext, args: dict) -> dict:
    return screen.type_text(args["text"])


@tool(
    "press_keys",
    "Press a key or a keyboard shortcut, e.g. 'enter', 'ctrl+s', 'cmd+space', 'alt+tab'.",
    {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]},
    destructive=True,
    preview=lambda a: f"Press {a.get('keys')}",
)
@_safe
def _press_keys(ctx: ToolContext, args: dict) -> dict:
    return screen.press_keys(args["keys"])


@tool(
    "scroll_screen",
    "Scroll the screen. Positive scrolls up, negative down.",
    {
        "type": "object",
        "properties": {"amount": {"type": "integer"}, "x": {"type": "integer"}, "y": {"type": "integer"}},
        "required": ["amount"],
    },
    destructive=True,
    preview=lambda a: f"Scroll {a.get('amount')}",
)
@_safe
def _scroll_screen(ctx: ToolContext, args: dict) -> dict:
    return screen.scroll(int(args["amount"]), args.get("x"), args.get("y"))


# ---------------------------------------------------------------------------
# Web fetch (reading the open web as part of a device task)
# ---------------------------------------------------------------------------


@tool(
    "fetch_url",
    "Fetch a web page or API over HTTP(S) and return its text. For reading a specific URL; "
    "use web_research to search.",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "method": {"type": "string", "enum": ["GET", "POST"]},
        },
        "required": ["url"],
    },
)
@_safe
def _fetch_url(ctx: ToolContext, args: dict) -> dict:
    import httpx

    url = args["url"]
    if not url.startswith(("http://", "https://")):
        return {"error": "Only http(s) URLs are supported."}
    try:
        resp = httpx.request(args.get("method") or "GET", url, timeout=20, follow_redirects=True)
    except httpx.HTTPError as exc:
        return {"error": f"Couldn't fetch the URL: {exc}"}
    body = resp.text
    if len(body) > 20_000:
        body = body[:20_000] + "\n…[truncated]…"
    return {"url": str(resp.url), "status": resp.status_code, "content": body}
