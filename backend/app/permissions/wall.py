"""The one switch for "computer protection" (an operating-system wall around the programs AURA starts).

While `active()` is False, every shell action needs the owner, every time, in every mode (the interim command
policy). A later package builds the wall and turns this on. Nothing else reads wall state: every place that needs
it calls `active()` through this module, so a test can force it with `monkeypatch.setattr(wall, "active", ...)`.
"""

from __future__ import annotations

SHELL_TOOLS = ("run_command", "run_in_background", "send_process_input")

UNCHECKED_CODE = "UNCHECKED_COMMAND"
UNCHECKED_REASON = (
    "AURA cannot check what this command does. It runs with your account, so it can read, change or delete "
    "anything you can, including AURA's own settings and other programs' data. Allow it once only if you know "
    "what it does."
)
CARD_CODE = "WALL_INACTIVE"
STATUS_ASKS = "asks_until_protection"
LABEL_RULE = "Asks every time until computer protection is on"
LABEL_TOOL_WIDE = LABEL_RULE  # no command can be checked while the wall is off, so a tool-wide rule covers none either
LABEL_SYSTEM_WIDE = "Commands still ask every time until computer protection is on"
STATUS_LINE = "Computer protection: off. Commands ask every time."


def active() -> bool:
    return False


def commands_ask_every_time() -> bool:
    return not active()
