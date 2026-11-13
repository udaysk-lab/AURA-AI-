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


def describe() -> list[dict]:
    """Rows for GET /api/permissions/catalogue: {tool, system, kind, targets, always_user}."""
    return [
        {
            "tool": p.name,
            "system": p.system,
            "kind": p.kind,
            "targets": list(p.rule_target_kinds),
            "always_user": p.always_user,
        }
        for p in CATALOGUE.values()
        if not p.loop_internal
    ]
