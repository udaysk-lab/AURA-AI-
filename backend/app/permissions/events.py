"""The permission event log: hash-chained and signed (spec 10 section 2.6).

Every row carries the hash of the row before it (`prev_hash`) and an HMAC of its
own hash made with a key derived from the server secret. Editing, deleting or
re-ordering a row breaks the chain at that point; `verify` reports where.
Nothing in the code base updates or deletes these rows (an account deletion
cascades, which is the only way they go).

What is written (a DECISION of the spec): every non-allow decision, every allow of
a kind that is not a plain read, every review, every approval step and every
permission change. Plain allows of read kinds are not logged (volume).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import PermissionEvent, utcnow

log = logging.getLogger("aura.permissions.events")

ACTORS = ("user", "aura", "system", "migration")
KINDS = (
    "decision", "review", "approval_created", "approval_resolved", "approval_expired",
    "mode_changed", "rule_added", "rule_revoked", "folder_added", "folder_changed",
    "folder_removed", "app_granted", "app_revoked", "block_added", "block_removed",
    "system_changed", "schedule_mode_changed", "boundaries_changed", "self_change_blocked",
    "legacy_mapped",
)
MAX_RETRIES = 5

_FIELDS = ("user_id", "seq", "actor", "kind", "tool", "target", "outcome", "reason_code", "run_key", "detail")


# ---------------------------------------------------------------------------
# Keys and hashing
# ---------------------------------------------------------------------------


def _hkdf(secret: bytes, info: bytes, length: int = 32) -> bytes:
    prk = hmac.new(b"\x00" * 32, secret, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def _mac_key() -> bytes:
    return _hkdf(settings.secret_key.encode("utf-8"), b"aura-permission-log")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _stamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _digest(prev_hash: str, fields: dict, created: datetime) -> str:
    body = canonical({**fields, "created_at": _stamp(created)})
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def _sign(hash_: str) -> str:
    return hmac.new(_mac_key(), hash_.encode("ascii"), hashlib.sha256).hexdigest()


def _row_fields(row: PermissionEvent) -> dict:
    return {name: getattr(row, name) for name in _FIELDS}


# ---------------------------------------------------------------------------
# Redaction (a small local one; the shared redactor of the task loop may replace it)
# ---------------------------------------------------------------------------

_SECRET_KV = re.compile(
    r"(?i)\b(pass(?:word|wd)?|secret|token|api[_-]?key|authorization|bearer|credential)s?\b(\s*[=:]\s*|\s+)([^\s'\",;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s'\",;]+")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+_=\-]{32,}")


_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _mask_token(m: re.Match) -> str:
    """A long unbroken run that looks like a key or token (not a long word, path segment or an id)."""
    t = m.group(0)
    if _UUID.fullmatch(t):  # row ids are what an audit trail is for
        return t
    mixed = bool(re.search(r"\d", t)) and bool(re.search(r"[A-Za-z]", t))
    padding = len(t) >= 40 and len(set(t)) < 6
    return "[redacted]" if mixed or padding else t


def redact_text(text: str, limit: int | None = None) -> str:
    out = _BEARER.sub("Bearer [redacted]", text or "")
    out = _SECRET_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", out)
    out = _LONG_TOKEN.sub(_mask_token, out)
    return out[:limit] if limit else out


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if isinstance(value, str):
        return redact_text(value, 500)
    if isinstance(value, dict):
        return {str(k)[:60]: redact(v, depth + 1) for k, v in list(value.items())[:30]}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in list(value)[:30]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value), 200)


# ---------------------------------------------------------------------------
# Append / verify / list
# ---------------------------------------------------------------------------


def append(
    db: Session,
    user_id: str,
    kind: str,
    *,
    actor: str = "system",
    tool: str = "",
    target: str = "",
    outcome: str = "",
    reason_code: str = "",
    run_key: str = "",
    detail: dict | None = None,
) -> PermissionEvent:
    """Add one event to the user's chain and commit it. Raises RuntimeError if it cannot be written."""
    clean = json.loads(canonical(redact(detail or {})))
    for attempt in range(MAX_RETRIES):
        last = db.scalars(
            select(PermissionEvent)
            .where(PermissionEvent.user_id == user_id)
            .order_by(PermissionEvent.seq.desc())
            .limit(1)
        ).first()
        seq = (last.seq if last else 0) + 1
        prev = last.hash if last else ""
        created = utcnow()
        row = PermissionEvent(
            user_id=user_id, seq=seq, actor=actor, kind=kind,
            tool=tool[:120], target=redact_text(target, 500), outcome=outcome[:8],
            reason_code=reason_code[:40], run_key=run_key[:36], detail=clean,
            prev_hash=prev, created_at=created, updated_at=created,
        )
        row.hash = _digest(prev, _row_fields(row), created)
        row.mac = _sign(row.hash)
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            # Another request took this seq (serverless concurrency): look again.
            if row in db:
                db.expunge(row)
            continue
        db.commit()
        return row
    raise RuntimeError("could not append to the permission event log")


def verify(db: Session, user_id: str) -> dict:
    """Recompute the user's chain: {"ok", "checked", "first_bad_seq"}."""
    rows = db.scalars(
        select(PermissionEvent).where(PermissionEvent.user_id == user_id).order_by(PermissionEvent.seq)
    ).all()
    prev, expected = "", 1
    for row in rows:
        created = row.created_at
        good = (
            row.seq == expected
            and row.prev_hash == prev
            and row.hash == _digest(prev, _row_fields(row), created)
            and hmac.compare_digest(row.mac or "", _sign(row.hash))
        )
        if not good:
            return {"ok": False, "checked": expected - 1, "first_bad_seq": row.seq if row.seq == expected else expected}
        prev, expected = row.hash, expected + 1
    return {"ok": True, "checked": len(rows), "first_bad_seq": None}


def listing(db: Session, user_id: str, before_seq: int | None = None, limit: int = 50) -> list[PermissionEvent]:
    q = select(PermissionEvent).where(PermissionEvent.user_id == user_id)
    if before_seq is not None:
        q = q.where(PermissionEvent.seq < before_seq)
    return list(db.scalars(q.order_by(PermissionEvent.seq.desc()).limit(max(1, min(limit, 200)))).all())
