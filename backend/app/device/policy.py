"""Shell command policy.

The autonomy tier decides whether a command needs your approval. This module
decides two narrower things on top of that:

* **Is a command flat-out refused?** `device_command_denylist` is the last stop
  against a catastrophic instruction — from you in a bad moment, or from a web
  page the agent read. Empty by default, because "operate the whole machine"
  should mean it; populate it to fence a machine off.

* **Can a command skip approval under `device_exec_mode=allowlist`?** So "git
  status" and "ls" run freely while anything else still asks.

It also flags obviously dangerous commands so the approval card can warn you in
words ("this deletes files recursively") rather than making you parse the shell
yourself. The flag is advisory — it changes the wording, not the gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import settings

# Patterns worth a louder warning on the approval card. Advisory only.
_DANGER = [
    (r"\brm\s+-[a-z]*r[a-z]*f?|\brm\s+-[a-z]*f", "deletes files recursively/forcibly"),
    (r"\brmdir\s+/s|\bdel\s+/[sq]|Remove-Item.+-Recurse", "deletes a folder tree"),
    (r"\bmkfs\b|\bformat\s|\bdiskpart\b", "formats a disk"),
    (r"\bdd\s+.*of=/dev/", "writes directly to a disk device"),
    (r">\s*/dev/sd|>\s*/dev/disk", "overwrites a disk device"),
    (r"\b(shutdown|reboot|halt|poweroff)\b|Stop-Computer|Restart-Computer", "powers off or reboots"),
    (r"\b(curl|wget|iwr|Invoke-WebRequest).+\|\s*(sudo\s+)?(bash|sh|python|powershell|pwsh|iex)",
     "pipes something downloaded straight into a shell"),
    (r"Invoke-Expression|\biex\b", "runs a string as code"),
    (r"\bchmod\s+-R\b|\bchown\s+-R\b", "changes permissions/ownership recursively"),
    (r":\(\)\s*\{\s*:\|\s*:", "is a fork bomb"),
    (r"\bgit\s+push\b.*--force|\bgit\s+reset\s+--hard", "force-pushes or hard-resets a repo"),
    (r"\breg\s+delete\b|Remove-ItemProperty.+HK", "deletes a registry key"),
]


@dataclass
class Verdict:
    allowed: bool              # False => refuse outright (denylist)
    auto: bool                 # True  => may run without approval (allowlist/full)
    dangerous: bool            # True  => warn loudly on the card
    reason: str = ""           # why refused, or what makes it dangerous


def classify(command: str) -> Verdict:
    """Policy verdict for one shell command. Does not consider the autonomy
    tier — the agent loop combines this with the tier and standing grants."""
    cmd = (command or "").strip()
    if not cmd:
        return Verdict(allowed=False, auto=False, dangerous=False, reason="empty command")

    lowered = cmd.lower()

    # 1. Hard denylist — refused no matter the tier, grant, or approval.
    for needle in settings.device_command_deny_list:
        if needle in lowered:
            return Verdict(
                allowed=False,
                auto=False,
                dangerous=True,
                reason=f"matches the command denylist ({needle!r})",
            )

    # 2. Danger flags — advisory, for the approval card wording.
    dangerous, why = False, ""
    for pattern, explanation in _DANGER:
        if re.search(pattern, cmd, re.IGNORECASE):
            dangerous, why = True, explanation
            break

    # 3. May it skip approval?
    auto = False
    if settings.device_exec_mode == "full":
        auto = True
    elif settings.device_exec_mode == "allowlist":
        auto = _allowlisted(cmd) and not dangerous

    return Verdict(allowed=True, auto=auto, dangerous=dangerous, reason=why)


def _allowlisted(command: str) -> bool:
    """True if the command starts with an allowlisted prefix.

    Prefix match on whitespace boundaries, so "git status" allows "git status
    --short" but not "git stash". A lone "git" would allow all of git, which is
    the operator's call to make.
    """
    cmd = command.strip()
    for prefix in settings.device_exec_allow_list:
        p = prefix.strip()
        if not p:
            continue
        if cmd == p or cmd.startswith(p + " "):
            return True
    return False


def describe_mode() -> str:
    mode = settings.device_exec_mode
    return {
        "ask": "Every command is queued for your approval unless you grant it standing permission.",
        "allowlist": "Allowlisted commands run on their own; everything else is queued for approval.",
        "full": "Commands run without approval (the denylist still refuses catastrophic ones).",
    }.get(mode, mode)
