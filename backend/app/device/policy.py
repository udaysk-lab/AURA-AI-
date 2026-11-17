"""Shell command policy.

The permission mode and rules decide whether a command needs your approval. This module
decides one narrower thing on top of that:

* **Is a command flat-out refused?** `device_command_denylist` is the last stop
  against a catastrophic instruction — from you in a bad moment, or from a web
  page the agent read. Empty by default, because "operate the whole machine"
  should mean it; populate it to fence a machine off.

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
    dangerous: bool            # True  => warn loudly on the card
    reason: str = ""           # why refused, or what makes it dangerous


def classify(command: str) -> Verdict:
    """Policy verdict for one shell command. Does not consider the permission
    mode — the agent loop combines this with the mode and standing grants."""
    cmd = (command or "").strip()
    if not cmd:
        return Verdict(allowed=False, dangerous=False, reason="empty command")

    lowered = cmd.lower()

    # 1. Hard denylist — refused no matter the mode, grant, or approval.
    for needle in settings.device_command_deny_list:
        if needle in lowered:
            return Verdict(
                allowed=False,
                dangerous=True,
                reason=f"matches the command denylist ({needle!r})",
            )

    # 2. Danger flags — advisory, for the approval card wording.
    dangerous, why = False, ""
    for pattern, explanation in _DANGER:
        if re.search(pattern, cmd, re.IGNORECASE):
            dangerous, why = True, explanation
            break

    return Verdict(allowed=True, dangerous=dangerous, reason=why)
