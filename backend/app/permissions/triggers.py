"""What each trigger may never do (spec 10 section 9.1, step 4 of decide()).

Unattended work gets a smaller toolbox than a chat the user is watching: nothing
that drives the screen or clipboard or opens apps; automations, inbound messages
and the heartbeat get less still. Returns a (code, reason) denial or None.
"""

from __future__ import annotations

from app.permissions.catalogue import ALLOW_KINDS, ToolPolicy

_SCREEN_ETC = frozenset({"screen_read", "screen_input", "clipboard_read", "clipboard_write", "app"})
_UNATTENDED_NO_EXEC = _SCREEN_ETC | {"exec", "process", "file_delete"}

DENIED_KINDS: dict[str, frozenset[str]] = {
    "schedule": _SCREEN_ETC,
    "automation": _UNATTENDED_NO_EXEC,
    "inbound": _UNATTENDED_NO_EXEC,
}

_WHY = {
    "schedule": "A schedule runs while you are away, so it cannot use the screen, the clipboard or open apps.",
    "automation": "An automation runs while you are away, so it cannot run commands, use the screen, the clipboard, open apps, or delete files.",
    "inbound": "A message from a channel cannot run commands, use the screen, the clipboard, open apps, or delete files.",
    "heartbeat": "AURA's own background checks only read, and write to AURA's own workspace.",
}


def check(policy: ToolPolicy, ctx) -> tuple[str, str] | None:
    trigger = ctx.trigger
    if trigger == "heartbeat":
        if policy.kind not in ALLOW_KINDS or policy.system == "device-control":
            return "TRIGGER_DENIED", _WHY["heartbeat"]
        return None
    if policy.kind in DENIED_KINDS.get(trigger, frozenset()):
        return "TRIGGER_DENIED", _WHY[trigger]
    return None
