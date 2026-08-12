"""Credential vault.

The design constraint: **the model never sees a secret value.** Not in a prompt,
not in a tool argument, not in a tool result, not in a log.

How that's enforced:
  * Values are encrypted at rest with the same Fernet key used for OAuth tokens.
  * The API never returns a value — only a key, a label and a masked hint.
  * Tools reference a secret as `{{vault:stripe_key}}`. The model can write that
    reference because it's just a name; it can't write the value because it has
    never been given it.
  * `resolve()` runs deterministically, in Python, immediately before an
    outbound call, and its output goes to the network — never back into the
    conversation.

That last point is the whole trick. Substitution happens after the model has
finished deciding and before the request leaves the process.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import VaultSecret, utcnow
from app.security import decrypt_secret, encrypt_secret

log = logging.getLogger("aura.vault")

REFERENCE = re.compile(r"\{\{vault:([a-zA-Z0-9_.-]{1,80})\}\}")
KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{1,80}$")


def _hint(value: str) -> str:
    """A masked fingerprint so the user can tell which secret is which."""
    if len(value) <= 6:
        return "•" * len(value)
    return f"{value[:3]}…{value[-3:]}"


def put(
    db: Session,
    user_id: str,
    key: str,
    value: str,
    label: str = "",
    kind: str = "secret",
) -> VaultSecret:
    if not KEY_PATTERN.match(key):
        raise ValueError("Key must be letters, numbers, dot, dash or underscore")

    row = db.scalars(
        select(VaultSecret).where(VaultSecret.user_id == user_id, VaultSecret.key == key)
    ).first()
    if row is None:
        row = VaultSecret(user_id=user_id, key=key)
        db.add(row)

    row.label = label or key
    row.kind = kind
    row.value_enc = encrypt_secret(value)
    row.hint = _hint(value)
    db.commit()
    db.refresh(row)
    return row


def delete(db: Session, user_id: str, key: str) -> bool:
    row = db.scalars(
        select(VaultSecret).where(VaultSecret.user_id == user_id, VaultSecret.key == key)
    ).first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def listing(db: Session, user_id: str) -> list[VaultSecret]:
    """Metadata only. There is deliberately no endpoint that returns a value."""
    return list(
        db.scalars(
            select(VaultSecret)
            .where(VaultSecret.user_id == user_id)
            .order_by(VaultSecret.key)
        ).all()
    )


def known_keys(db: Session, user_id: str) -> list[str]:
    """Key names are safe to show the model — that's how it writes references."""
    return [row.key for row in listing(db, user_id)]


def resolve(db: Session, user_id: str, text: str) -> str:
    """Replace {{vault:key}} references with real values.

    Call this at the last possible moment, on data heading out of the process.
    Never call it on anything that will be returned to the model or stored.
    """
    if not text or "{{vault:" not in text:
        return text

    def swap(match: re.Match) -> str:
        key = match.group(1)
        row = db.scalars(
            select(VaultSecret).where(
                VaultSecret.user_id == user_id, VaultSecret.key == key
            )
        ).first()
        if not row:
            log.warning("Unresolved vault reference %r", key)
            return match.group(0)
        row.use_count += 1
        row.last_used_at = utcnow()
        return decrypt_secret(row.value_enc)

    resolved = REFERENCE.sub(swap, text)
    db.commit()
    return resolved


def resolve_mapping(db: Session, user_id: str, data: dict) -> dict:
    """Recursively resolve references in a dict of outbound request data."""
    out: dict = {}
    for key, value in data.items():
        if isinstance(value, str):
            out[key] = resolve(db, user_id, value)
        elif isinstance(value, dict):
            out[key] = resolve_mapping(db, user_id, value)
        elif isinstance(value, list):
            out[key] = [
                resolve(db, user_id, v) if isinstance(v, str) else v for v in value
            ]
        else:
            out[key] = value
    return out


def redact(db: Session, user_id: str, text: str) -> str:
    """Belt and braces: strip any literal secret that leaked into a string."""
    if not text:
        return text
    for row in listing(db, user_id):
        value = decrypt_secret(row.value_enc)
        if value and len(value) >= 8 and value in text:
            text = text.replace(value, f"{{{{vault:{row.key}}}}}")
    return text


def prompt_block(db: Session, user_id: str) -> str:
    """What the model is told about the vault: names only, never values."""
    keys = known_keys(db, user_id)
    if not keys:
        return ""
    return (
        "Stored credentials you may reference by name (you cannot read their values; "
        "write them as {{vault:name}} and the system substitutes them at send time): "
        + ", ".join(keys)
    )
