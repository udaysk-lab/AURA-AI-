"""The two questions that always need the owner, whatever the mode (docs/PERMISSIONS.md; the companion of wall.py).

UNCHECKED_OPEN  `open_app` with a file that may run as a program when the system opens it.
SCREEN_INPUT    every tool that moves the pointer, clicks, scrolls, types or presses keys.

Both are decided in the gate before ask rules, allow rules, defaults, the mode and the reviewer, so none of them
can see them. Screen input has exactly one allowance: a 10 minute window bound to one application.
"""

from __future__ import annotations

# --- open_app with a file ---------------------------------------------------
UNCHECKED_OPEN = "UNCHECKED_OPEN"
UNCHECKED_OPEN_REASON = (
    "Opening this file may run it as a program, and AURA cannot check what it does. It would run with your "
    "account, so it could read, change or delete anything you can. Allow it once only if you know what the file is."
)

# --- screen input -----------------------------------------------------------
SCREEN_INPUT = "SCREEN_INPUT"
SCREEN_TOOLS = ("mouse_click", "scroll_screen", "type_text", "press_keys")
WINDOW_MINUTES = 10
SCREEN_WINDOW_SOURCE = "screen"  # PermissionRule.source of a 10 minute window; never offered or editable as a rule
SCREEN_WINDOW_CODE = "SCREEN_WINDOW"


def screen_reason(app: str) -> str:
    return (
        f"For this time AURA can do in {app} whatever you could do there with the mouse and keyboard, including "
        f"starting programs. Watch what it does, and stop it if it goes wrong."
    )


def screen_window_reason(app: str) -> str:
    return f"You allowed AURA to use the mouse and keyboard in {app} for {WINDOW_MINUTES} minutes."


# --- the line Settings > Permissions shows for these tools --------------------
NOTE_OPEN = ("Documents, media and folders follow your mode and rules. Anything that could run as a program "
             "(scripts, shortcuts, installers, macro documents, unknown files) asks you every time.")
NOTE_SCREEN = ("Asks you every time, in every mode: once, or for 10 minutes in one application. "
               "Skip approvals, rules and Auto review do not cover it.")
NOTE_SCREENSHOT = "Follows your mode and rules."
