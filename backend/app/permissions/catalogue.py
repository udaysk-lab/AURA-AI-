"""The declarative tool catalogue (spec 10-permissions.md section 3).

Every tool in `agent.tools.REGISTRY` has exactly one entry here: which system
(plugin) owns it, what kind of action it is, which of its arguments name a
target that rules can be limited to, and whether it always needs the user.

Fail closed: a tool without an entry is not offered to the model
(`agent.skills.enabled_tools`) and the gate denies it. Adding a tool means adding
its entry; `tests/test_perm_catalogue.py` fails until you do.

No imports from the agent or the database, so this loads anywhere.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

log = logging.getLogger("aura.permissions")

# --- kinds and their default disposition (spec 3.1) -------------------------

READ_KINDS = frozenset({"read", "file_read", "screen_read", "clipboard_read"})
ALLOW_KINDS = READ_KINDS | {"workspace"}
REVIEWED_KINDS = frozenset({
    "workspace_delete", "send", "external", "file_write", "exec", "process", "app",
    "screen_input", "clipboard_write", "network",
})
ALWAYS_USER_KINDS = frozenset({"file_delete", "settings"})
# "settings" is deliberately NOT a kind: nothing a tool does may change permissions,
# rules, systems, schedules or settings. A kind that does must be always-user.
KINDS = ALLOW_KINDS | REVIEWED_KINDS | {"file_delete"}

TARGET_KINDS = frozenset({"path", "command", "recipient", "url", "app", "pid"})

# kind of target -> the target_kind a stored rule uses
RULE_KIND_FOR_TARGET = {"path": "path", "command": "command", "recipient": "recipient", "url": "domain", "app": "app", "pid": "app"}

MODES = ("ask", "auto", "skip")


def default_disposition(kind: str, mode: str) -> str:
    """What a kind does with no rule: allow | ask | reviewer | always_user."""
    if kind in ALLOW_KINDS:
        return "allow"
    if kind in ALWAYS_USER_KINDS:
        return "always_user"
    if kind not in REVIEWED_KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if mode == "skip":
        return "allow"
    if mode == "auto":
        return "reviewer"
    return "ask"


# --- entries ----------------------------------------------------------------


@dataclass(frozen=True)
class TargetSpec:
    kind: str  # path | command | recipient | url | app | pid
    arg: str  # name of the argument that holds it ("" for "the frontmost app")
    role: str = ""  # path: read | write | delete | exec (a working directory); app: frontmost
    many: bool = False  # a list, or a comma-separated list
    default: str | None = None  # used when the argument is absent; None = absent means no target

    def __post_init__(self) -> None:
        if self.kind not in TARGET_KINDS:
            raise ValueError(f"unknown target kind {self.kind!r}")


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    system: str
    kind: str
    targets: tuple[TargetSpec, ...] = ()
    always_user: bool = False
    loop_internal: bool = False
    # run_command / run_in_background: always-user for a call that deletes files or
    # touches a protected location (decided per call from the parsed command).
    dyn_always_user: bool = False
    # Rule kinds a call can also be limited to, when an argument may hold either of two things
    # (open_app takes an app name or a URL, F14).
    extra_rule_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.name}: unknown kind {self.kind!r}")
        if self.kind in ALWAYS_USER_KINDS and not self.always_user:
            raise ValueError(f"{self.name}: kind {self.kind!r} must be always_user")

    @property
    def rule_target_kinds(self) -> tuple[str, ...]:
        """The target kinds a rule on this tool can be limited to, in order."""
        out: list[str] = []
        for t in self.targets:
            if t.role == "exec":
                continue
            k = RULE_KIND_FOR_TARGET[t.kind]
            if k not in out:
                out.append(k)
        out.extend(k for k in self.extra_rule_kinds if k not in out)
        return tuple(out)


def _p(name, system, kind, *targets, **kw) -> ToolPolicy:
    return ToolPolicy(name, system, kind, tuple(targets), **kw)


_PATH_R = TargetSpec("path", "path", "read")
_PATH_W = TargetSpec("path", "path", "write")
_FRONT = TargetSpec("app", "", "frontmost")

_ENTRIES: list[ToolPolicy] = [
    _p("summarize_inbox", "core-inbox", "read"),
    _p("search_emails", "core-inbox", "read"),
    _p("get_email", "core-inbox", "read"),
    _p("draft_reply", "core-inbox", "read"),
    _p("send_email", "core-inbox", "send", TargetSpec("recipient", "to", many=True)),
    _p("archive_email", "core-inbox", "external"),
    _p("list_events", "core-calendar", "read"),
    _p("find_free_time", "core-calendar", "read"),
    _p("detect_conflicts", "core-calendar", "read"),
    _p("prepare_meeting", "core-calendar", "read"),
    _p("create_event", "core-calendar", "send", TargetSpec("recipient", "attendees", many=True)),
    _p("delete_event", "core-calendar", "external"),
    _p("list_tasks", "core-tasks", "read"),
    _p("create_task", "core-tasks", "workspace"),
    _p("complete_task", "core-tasks", "workspace"),
    _p("delete_task", "core-tasks", "workspace_delete"),
    _p("save_memory", "core-memory", "workspace"),
    _p("search_memory", "core-memory", "read"),
    _p("list_contacts", "core-memory", "read"),
    _p("get_daily_briefing", "core-planning", "read"),
    _p("sync_google", "core-planning", "read"),
    _p("notify", "core-planning", "workspace"),
    _p("web_research", "research", "read"),
    _p("brief_subject", "research", "read"),
    _p("search_documents", "documents", "read"),
    _p("summarize_document", "documents", "read"),
    _p("compare_documents", "documents", "read"),
    # The assignee is recorded, not a recipient: nothing is sent.
    _p("delegate_task", "delegation", "workspace"),
    _p("chase_delegation", "delegation", "workspace"),
    _p("list_delegations", "delegation", "read"),
    _p("check_focus_blocks", "focus", "read"),
    _p("run_command", "device-control", "exec", TargetSpec("command", "command"),
       TargetSpec("path", "workdir", "exec"), dyn_always_user=True),
    _p("run_in_background", "device-control", "exec", TargetSpec("command", "command"),
       TargetSpec("path", "workdir", "exec"), dyn_always_user=True),
    _p("send_process_input", "device-control", "exec"),  # never target-matchable
    _p("process_output", "device-control", "read"),
    _p("list_background_commands", "device-control", "read"),
    _p("stop_process", "device-control", "process"),
    _p("read_file", "device-control", "file_read", _PATH_R),
    # The spec names `path`; these two take it optionally and default to the home folder.
    _p("list_directory", "device-control", "file_read", TargetSpec("path", "path", "read", default="~")),
    _p("search_files", "device-control", "file_read", TargetSpec("path", "root", "read", default="~")),
    _p("write_file", "device-control", "file_write", _PATH_W),
    _p("edit_file", "device-control", "file_write", _PATH_W),
    _p("make_directory", "device-control", "file_write", _PATH_W),
    _p("move_file", "device-control", "file_write", TargetSpec("path", "source", "delete"),
       TargetSpec("path", "destination", "write")),
    _p("delete_file", "device-control", "file_delete", TargetSpec("path", "path", "delete"), always_user=True),
    _p("open_app", "device-control", "app", TargetSpec("app", "name"), extra_rule_kinds=("domain",)),
    _p("close_app", "device-control", "app", TargetSpec("app", "name")),
    _p("list_applications", "device-control", "read"),
    _p("system_info", "device-control", "read"),
    _p("list_processes", "device-control", "read"),
    _p("kill_process", "device-control", "process", TargetSpec("pid", "pid")),
    _p("read_clipboard", "device-control", "clipboard_read"),
    _p("set_clipboard", "device-control", "clipboard_write"),
    _p("desktop_notify", "device-control", "workspace"),
    _p("take_screenshot", "device-control", "screen_read", _FRONT),
    _p("mouse_click", "device-control", "screen_input", _FRONT),
    _p("scroll_screen", "device-control", "screen_input", _FRONT),
    _p("type_text", "device-control", "screen_input", _FRONT),
    _p("press_keys", "device-control", "screen_input", _FRONT),
    _p("fetch_url", "device-control", "network", TargetSpec("url", "url")),
]
# The task loop's own bookkeeping: handled inside the loop, never in REGISTRY.
_ENTRIES += [
    _p(n, "*", "workspace", loop_internal=True)
    for n in ("aura_plan", "aura_progress", "aura_check", "aura_outcome", "aura_note_preference")
]

CATALOGUE: dict[str, ToolPolicy] = {p.name: p for p in _ENTRIES}
assert len(CATALOGUE) == len(_ENTRIES), "duplicate catalogue entry"

# --- human phrases (one place for the words) ---------------------------------
# tool -> (action phrase with its article, for "AURA wants to <action>";
#          -ing phrase, for "Always allow <ing>"). Every catalogue entry needs both:
# tests/test_perm_catalogue.py fails until a new tool has them.

PHRASES: dict[str, tuple[str, str]] = {
    "summarize_inbox": ("summarize your inbox", "summarizing your inbox"),
    "search_emails": ("search your email", "searching email"),
    "get_email": ("read an email", "reading email"),
    "draft_reply": ("draft a reply", "drafting replies"),
    "send_email": ("send an email", "sending email"),
    "archive_email": ("archive an email", "archiving email"),
    "list_events": ("look at your calendar", "looking at your calendar"),
    "find_free_time": ("find free time in your calendar", "finding free time"),
    "detect_conflicts": ("check your calendar for clashes", "checking for calendar clashes"),
    "prepare_meeting": ("prepare for a meeting", "preparing for meetings"),
    "create_event": ("create a calendar event", "creating calendar events"),
    "delete_event": ("delete a calendar event", "deleting calendar events"),
    "list_tasks": ("look at your tasks", "looking at your tasks"),
    "create_task": ("create a task", "creating tasks"),
    "complete_task": ("mark a task as done", "marking tasks as done"),
    "delete_task": ("delete a task", "deleting tasks"),
    "save_memory": ("remember something about you", "saving things to memory"),
    "search_memory": ("search what it remembers", "searching memory"),
    "list_contacts": ("look up your contacts", "looking up contacts"),
    "get_daily_briefing": ("put together your daily briefing", "putting together your daily briefing"),
    "sync_google": ("sync your Google account", "syncing your Google account"),
    "notify": ("send you a notification", "sending you notifications"),
    "web_research": ("research something on the web", "researching on the web"),
    "brief_subject": ("write a briefing on a subject", "writing briefings on a subject"),
    "search_documents": ("search your documents", "searching documents"),
    "summarize_document": ("summarize a document", "summarizing documents"),
    "compare_documents": ("compare documents", "comparing documents"),
    "delegate_task": ("hand a task to someone", "handing tasks to people"),
    "chase_delegation": ("follow up on a handed-off task", "following up on handed-off tasks"),
    "list_delegations": ("look at the tasks you handed off", "looking at handed-off tasks"),
    "check_focus_blocks": ("check your focus time", "checking focus time"),
    "run_command": ("run a command", "running commands"),
    "run_in_background": ("run a command in the background", "running commands in the background"),
    "send_process_input": ("send input to a running command", "sending input to a running command"),
    "process_output": ("read the output of a running command", "reading the output of running commands"),
    "list_background_commands": ("list the commands running in the background", "listing background commands"),
    "stop_process": ("stop a process", "stopping processes"),
    "read_file": ("read a file", "reading files"),
    "list_directory": ("list the files in a folder", "listing the files in folders"),
    "search_files": ("search for files", "searching for files"),
    "write_file": ("write a file", "writing files"),
    "edit_file": ("edit a file", "editing files"),
    "make_directory": ("create a folder", "creating folders"),
    "move_file": ("move a file", "moving files"),
    "delete_file": ("delete a file", "deleting files"),
    "open_app": ("open an app", "opening apps"),
    "close_app": ("close an app", "closing apps"),
    "list_applications": ("see which apps are open", "seeing which apps are open"),
    "system_info": ("check this computer's details", "checking this computer's details"),
    "list_processes": ("list the running processes", "listing running processes"),
    "kill_process": ("end a process", "ending processes"),
    "read_clipboard": ("read your clipboard", "reading the clipboard"),
    "set_clipboard": ("change your clipboard", "changing the clipboard"),
    "desktop_notify": ("show a notification on your computer", "showing notifications on your computer"),
    "take_screenshot": ("take a screenshot", "taking screenshots"),
    "mouse_click": ("click with the mouse", "clicking with the mouse"),
    "scroll_screen": ("scroll the screen", "scrolling the screen"),
    "type_text": ("type on your keyboard", "typing"),
    "press_keys": ("press keys", "pressing keys"),
    "fetch_url": ("fetch a web page", "fetching web pages"),
    "aura_plan": ("make a plan", "making plans"),
    "aura_progress": ("update its progress", "updating its progress"),
    "aura_check": ("check its own work", "checking its own work"),
    "aura_outcome": ("report how it went", "reporting how it went"),
    "aura_note_preference": ("note a preference", "noting preferences"),
}


def action_phrase(tool_name: str) -> str | None:
    """"create a task": what completes "AURA wants to ...". None for a tool outside the catalogue."""
    p = PHRASES.get(tool_name)
    return p[0] if p else None


def ing_phrase(tool_name: str) -> str | None:
    """"creating tasks": what completes "Always allow ...". None for a tool outside the catalogue."""
    p = PHRASES.get(tool_name)
    return p[1] if p else None


def policy_for(tool_name: str) -> ToolPolicy | None:
    return CATALOGUE.get(tool_name)


def is_known(tool_name: str) -> bool:
    return tool_name in CATALOGUE


def is_read_only(tool_name: str) -> bool:
    p = CATALOGUE.get(tool_name)
    return bool(p and p.kind in READ_KINDS)


def missing_from_catalogue(tool_names: Iterable[str]) -> list[str]:
    """Registered tool names that have no entry (these fail closed)."""
    return sorted(n for n in tool_names if n not in CATALOGUE)


def log_missing(tool_names: Iterable[str]) -> list[str]:
    """Log an error for each registered tool without an entry; returns them."""
    missing = missing_from_catalogue(tool_names)
    for name in missing:
        log.error("tool %r has no permission catalogue entry; it is hidden and will be denied", name)
    return missing


def default_note(name: str) -> str:
    """One plain line for Settings > Permissions about a tool that does not simply follow mode and rules."""
    from app.permissions import asks

    if name in asks.SCREEN_TOOLS:
        return asks.NOTE_SCREEN
    if name == "open_app":
        return asks.NOTE_OPEN
    if name == "take_screenshot":
        return asks.NOTE_SCREENSHOT
    return ""


def describe() -> list[dict]:
    """Rows for GET /api/permissions/catalogue: {tool, system, kind, targets, always_user, action_text, note}."""
    return [
        {
            "tool": p.name,
            "system": p.system,
            "kind": p.kind,
            "targets": list(p.rule_target_kinds),
            "always_user": p.always_user,
            "action_text": action_phrase(p.name) or "",
            "note": default_note(p.name),
        }
        for p in CATALOGUE.values()
        if not p.loop_internal
    ]
