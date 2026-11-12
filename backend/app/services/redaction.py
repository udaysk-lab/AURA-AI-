"""Strip secrets before anything is written to task memory or preference memory.

Spec 20-taskloop-memory.md section 9.2. `scrub` first replaces the user's own stored
credentials (the vault) with their `{{vault:name}}` placeholders, then replaces anything that
looks like a credential with `[redacted]`. `scrub_json` does the same through a structure and
blanks the value of any key that names a secret.

Applied to: instruction, work-step arguments and summaries, plan, summary, outcome, report,
the result stored on a PendingAction, preference text and excerpt, and error text.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.services import vault

MARK = "[redacted]"

_PLACEHOLDER = r"\{\{vault:[^}]*\}\}"

_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
# A block whose END line is missing (cut off) is still a key: take the rest of the text.
_PRIVATE_KEY_OPEN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*", re.S)

# Order matters: sk-ant- before sk-.
_TOKENS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"),
    re.compile(r"Bearer\s+[\w.~+/-]{16,}", re.I),
]

# Keeps the key name and the separator: "password: hunter2" -> "password: [redacted]".
_KEY_VALUE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)(\s*[:=]\s*)(?!" + _PLACEHOLDER + r")(?!\[redacted\])\S+"
)

_LONG_BLOBS = [re.compile(r"[A-Fa-f0-9]{40,}"), re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")]
_NEAR_KEY = re.compile(r"(?i)key|secret|token|password")
_NEAR = 20

# JSON keys whose value is blanked outright. Matched on the key's words, so `access_token`
# and `api-key` match but `tokens_used` and `mapping` do not.
_SENSITIVE_WORDS = {"password", "passwd", "pwd", "passphrase", "secret", "token", "authorization",
                    "cookie", "otp", "pin"}
_VAULT_ONLY = re.compile(r"^\s*" + _PLACEHOLDER + r"\s*$")


def _near_key(text: str, start: int, end: int) -> bool:
    return bool(_NEAR_KEY.search(text[max(0, start - _NEAR):start]) or _NEAR_KEY.search(text[end:end + _NEAR]))


def _blobs(text: str) -> str:
    for pattern in _LONG_BLOBS:
        text = pattern.sub(lambda m: MARK if _near_key(m.string, m.start(), m.end()) else m.group(0), text)
    return text


def scrub_patterns(text: str) -> str:
    """The pattern half of `scrub`: no database, nothing user-specific."""
    if not text:
        return text
    text = _PRIVATE_KEY.sub(MARK, text)
    text = _PRIVATE_KEY_OPEN.sub(MARK, text)
    for pattern in _TOKENS:
        text = pattern.sub(MARK, text)
    text = _KEY_VALUE.sub(lambda m: m.group(1) + m.group(2) + MARK, text)
    return _blobs(text)


def scrub(db: Session | None, user_id: str, text: str) -> str:
    """Vault values become `{{vault:name}}`; credential-shaped text becomes `[redacted]`."""
    if not text:
        return text or ""
    text = str(text)
    if db is not None:
        text = vault.redact(db, user_id, text)
    return scrub_patterns(text)


def _sensitive_key(key: Any) -> bool:
    norm = re.sub(r"[^a-z0-9]+", "_", re.sub(r"([a-z])([A-Z])", r"\1_\2", str(key)).lower()).strip("_")
    if "api_key" in norm or "apikey" in norm:
        return True
    return bool(set(norm.split("_")) & _SENSITIVE_WORDS)


def scrub_json(db: Session | None, user_id: str, obj: Any, max_str: int = 300, _depth: int = 0) -> Any:
    """A scrubbed copy of a JSON-like structure.

    Values under a secret-looking key become `[redacted]` (a lone vault placeholder is kept);
    every string is scrubbed and cut to `max_str`; lists are cut to 50 entries and nesting
    to 8 levels.
    """
    if _depth > 8:
        return MARK
    if isinstance(obj, str):
        out = scrub(db, user_id, obj)
        return out if len(out) <= max_str else out[: max(0, max_str - 1)] + "…"
    if isinstance(obj, dict):
        clean: dict[str, Any] = {}
        for key, value in obj.items():
            if _sensitive_key(key) and not (isinstance(value, str) and _VAULT_ONLY.match(value)):
                clean[str(key)] = MARK if value not in (None, "", [], {}) else value
            else:
                clean[str(key)] = scrub_json(db, user_id, value, max_str, _depth + 1)
        return clean
    if isinstance(obj, (list, tuple)):
        return [scrub_json(db, user_id, v, max_str, _depth + 1) for v in list(obj)[:50]]
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    return scrub(db, user_id, str(obj))[:max_str]
