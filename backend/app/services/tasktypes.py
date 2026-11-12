"""Task typing: the fixed taxonomy that keys task memory and preference memory.

Spec 20-taskloop-memory.md section 4. A type is derived in three steps: `classify` on the
owner's words when a turn starts (used for recall), the model's own `aura_plan(task_type=...)`
through `normalise`, and `from_tools` on the tools that ran when the item is finalised.
Pure functions, no database, no model call.
"""

from __future__ import annotations

import re
from collections import Counter

# value -> label, in display order. `general` is valid only for preferences.
TYPES: dict[str, str] = {
    "email": "Email",
    "scheduling": "Calendar & scheduling",
    "todo": "To-dos",
    "research": "Research",
    "writing": "Writing & drafting",
    "documents": "Documents",
    "files": "Files & folders",
    "commands": "Commands & code",
    "computer": "Apps & screen",
    "contacts": "People & contacts",
    "briefing": "Briefings & summaries",
    "delegation": "Delegation & follow-ups",
    "other": "Other",
}
GENERAL = "general"
PREFERENCE_TYPES: tuple[str, ...] = (*TYPES, GENERAL)

# A word may carry a plain inflection ("emails", "scheduled", "booking"); it may not run on
# into another word ("postal" is not "post").
_TAIL = r"(?:s|es|d|ed|ing)?"


def _words(*alternatives: str) -> str:
    return r"\b(?:" + "|".join(alternatives) + r")" + _TAIL + r"\b"


# First match wins, so the order is part of the contract.
KEYWORDS: list[tuple[str, re.Pattern]] = [
    ("commands", re.compile(
        _words("run", "command", "terminal", "shell", "script", "git", "npm", "pip", "install", "build", "compile", "deploy"),
        re.I)),
    ("files", re.compile(
        _words("file", "folder", "directory", "rename", r"move .* to", r"copy .* to", "downloads")
        + r"|\.(?:pdf|docx|csv)\b", re.I)),
    ("computer", re.compile(_words(r"open (?:the )?app", "click", "screen", "window", r"close (?:the )?app"), re.I)),
    ("email", re.compile(
        _words("email", "e-mail", "inbox", "reply", "mail", r"send .* to", "forward", "unread"), re.I)),
    ("scheduling", re.compile(
        _words("meeting", "calendar", "schedule", "reschedule", "book", "event", "free time", "availability", "invite"),
        re.I)),
    ("todo", re.compile(_words("task", "to-?do", "remind me", "checklist", r"mark .* done"), re.I)),
    ("delegation", re.compile(_words("delegate", "chase", "follow up with", "hand off"), re.I)),
    ("research", re.compile(
        _words("research", "look up", "find out", r"compare .* (?:prices|options)", "search the web"), re.I)),
    ("documents", re.compile(
        _words("document", "contract", "report", r"summari[sz]e (?:the|this) (?:doc|pdf|file)"), re.I)),
    ("briefing", re.compile(
        _words("brief", "briefing", "catch me up", "my day", r"summary of (?:today|the week)"), re.I)),
    ("writing", re.compile(_words("write", "draft", "rewrite", "proofread", "post", "blog", "caption"), re.I)),
    ("contacts", re.compile(_words("contact", "phone number", "who is", "address of"), re.I)),
]

# Tool name -> type. Tools left out (memory, notify, sync, focus guard, clipboard, fetch) carry no type.
TOOL_TYPES: dict[str, str] = {
    # EM01, EM02, EM03
    "summarize_inbox": "email", "search_emails": "email", "get_email": "email",
    "draft_reply": "email", "send_email": "email", "archive_email": "email",
    # CA01, CA02, CA03, MP01
    "list_events": "scheduling", "create_event": "scheduling", "delete_event": "scheduling",
    "find_free_time": "scheduling", "detect_conflicts": "scheduling", "prepare_meeting": "scheduling",
    # TK01, TK02
    "create_task": "todo", "complete_task": "todo", "delete_task": "todo", "list_tasks": "todo",
    # DL01, DL02
    "delegate_task": "delegation", "list_delegations": "delegation", "chase_delegation": "delegation",
    # RS01, RS02
    "web_research": "research", "brief_subject": "research",
    # DC01, DC02
    "search_documents": "documents", "summarize_document": "documents", "compare_documents": "documents",
    # CT01, BR01
    "list_contacts": "contacts", "get_daily_briefing": "briefing",
    # DV01 commands
    "run_command": "commands", "run_in_background": "commands", "process_output": "commands",
    "send_process_input": "commands", "stop_process": "commands", "list_background_commands": "commands",
    # DV02 files
    "read_file": "files", "write_file": "files", "edit_file": "files", "list_directory": "files",
    "search_files": "files", "move_file": "files", "delete_file": "files", "make_directory": "files",
    # DV03, DV04 and the screen tools (DV05)
    "open_app": "computer", "close_app": "computer", "list_applications": "computer",
    "system_info": "computer", "list_processes": "computer", "kill_process": "computer",
    "take_screenshot": "computer", "mouse_click": "computer", "type_text": "computer",
    "press_keys": "computer", "scroll_screen": "computer",
}

_ALIASES: dict[str, str] = {
    "mail": "email", "emails": "email", "e_mail": "email", "inbox": "email",
    "calendar": "scheduling", "meeting": "scheduling", "meetings": "scheduling", "schedule": "scheduling",
    "calendar_and_scheduling": "scheduling", "calendar_scheduling": "scheduling",
    "task": "todo", "tasks": "todo", "to_do": "todo", "to_dos": "todo", "todos": "todo", "to_do_s": "todo",
    "search": "research", "web_research": "research",
    "write": "writing", "draft": "writing", "drafting": "writing", "writing_and_drafting": "writing",
    "document": "documents", "doc": "documents", "docs": "documents",
    "file": "files", "folder": "files", "folders": "files", "files_and_folders": "files",
    "command": "commands", "commands_and_code": "commands", "code": "commands", "shell": "commands", "terminal": "commands",
    "app": "computer", "apps": "computer", "screen": "computer", "apps_and_screen": "computer",
    "contact": "contacts", "people": "contacts", "people_and_contacts": "contacts",
    "brief": "briefing", "briefings": "briefing", "summary": "briefing", "briefings_and_summaries": "briefing",
    "delegate": "delegation", "follow_up": "delegation", "follow_ups": "delegation",
    "delegation_and_follow_ups": "delegation",
}


def classify(text: str) -> str:
    """The type of an instruction by its words; `other` when nothing matches."""
    text = (text or "")[:2000]
    for value, pattern in KEYWORDS:
        if pattern.search(text):
            return value
    return "other"


def from_tools(tool_names: list[str]) -> str:
    """The most frequent type among the tools that ran; ties go to the one used first."""
    counts: Counter[str] = Counter()
    first: dict[str, int] = {}
    for i, name in enumerate(tool_names or []):
        value = TOOL_TYPES.get(name)
        if value:
            counts[value] += 1
            first.setdefault(value, i)
    if not counts:
        return "other"
    return min(counts, key=lambda v: (-counts[v], first[v]))


def normalise(value: str | None, *, allow_general: bool = False) -> str:
    """A taxonomy value from whatever the model or a client wrote; unknown becomes `other`.

    `general` is kept only when `allow_general` (preferences); otherwise it is `other`.
    """
    key = re.sub(r"[\s\-]+", "_", (value or "").strip().lower()).strip("_")
    if key == GENERAL:
        return GENERAL if allow_general else "other"
    if key in TYPES:
        return key
    return _ALIASES.get(key, "other")


def label(value: str) -> str:
    return TYPES.get(value, "General" if value == GENERAL else TYPES["other"])


def options() -> list[dict[str, str]]:
    """The taxonomy as the `/api/task-types` list."""
    return [{"value": v, "label": l} for v, l in TYPES.items()]


def from_skill_code(code: str) -> str:
    """The type a skill's first tool carries (for `skills.teach`); `general` when it has none."""
    from app.agent import skills as skill_registry

    skill = skill_registry.CATALOG.get((code or "").upper())
    if skill is None:
        return GENERAL
    for tool in skill.tools:
        if tool in TOOL_TYPES:
            return TOOL_TYPES[tool]
        break  # "first tool", as the spec words it
    return GENERAL
