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
import os
import re
import secrets
import threading
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


LOG_KEY_FILE = "event-log.key"
HEADS_FILE = "event-heads.json"
_key_lock = threading.Lock()
_key_cache: dict[str, bytes] = {}


def _tighten(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _local_log_key() -> bytes:
    """F19: on a local install the log is signed with a key of its own, kept owner-only in AURA's
    home (a folder the file tools are fenced out of). It is made on first use. Not being able to
    have one is an error: the caller then refuses to record, and so refuses to act."""
    path = os.path.join(settings.aura_home_path, LOG_KEY_FILE)
    with _key_lock:
        cached = _key_cache.get(path)
        if cached:
            return cached
        key = b""
        try:
            with open(path, "r", encoding="ascii") as fh:
                text = fh.read().strip()
            if len(text) == 64:
                key = bytes.fromhex(text)
        except (OSError, ValueError):
            key = b""
        if not key:
            key = secrets.token_bytes(32)
            tmp = f"{path}.{secrets.token_hex(4)}.tmp"
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="ascii") as fh:
                    fh.write(key.hex())
                _tighten(tmp)
                os.replace(tmp, path)
            except OSError as exc:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise RuntimeError("the permission log's signing key could not be kept") from exc
        _tighten(path)
        _key_cache[path] = key
        return key


def _mac_key() -> bytes:
    if settings.local_mode:
        return _local_log_key()
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
# The head, kept in a second place (F19)
# ---------------------------------------------------------------------------
# The newest entry's number and hash are also written to a file in AURA's home, signed. Cutting
# entries off the end of the table leaves a chain that still verifies; the file no longer matches.
# Someone who can write both the table and that file, and holds the signing key, can still rewrite
# the whole record: this makes a quiet truncation visible, it does not make the log tamper-proof.

_heads_lock = threading.Lock()


def _heads_path() -> str:
    return os.path.join(settings.aura_home_path, HEADS_FILE)


def _head_mac(user_id: str, seq: int, hash_: str) -> str:
    return _sign(f"head:{user_id}:{seq}:{hash_}")


def _read_heads() -> dict:
    try:
        with open(_heads_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_head(user_id: str, seq: int, hash_: str) -> None:
    """Best effort: a place that cannot be written (a read-only disk) just means no second copy."""
    try:
        with _heads_lock:
            heads = _read_heads()
            heads[user_id] = {"seq": seq, "hash": hash_, "mac": _head_mac(user_id, seq, hash_)}
            path = _heads_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{secrets.token_hex(4)}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(heads, fh)
            os.replace(tmp, path)
    except (OSError, RuntimeError) as exc:
        log.debug("could not keep the log head: %s", exc)


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
        _write_head(user_id, row.seq, row.hash)
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
    head = _read_heads().get(user_id)
    if isinstance(head, dict):
        seq, hash_ = head.get("seq"), head.get("hash")
        if isinstance(seq, int) and isinstance(hash_, str) and hmac.compare_digest(
            str(head.get("mac") or ""), _head_mac(user_id, seq, hash_)
        ):
            have = len(rows)
            if have < seq:
                # entries are missing from the end
                return {"ok": False, "checked": have, "first_bad_seq": have + 1, "reason": "entries_missing"}
            if have >= seq and seq >= 1 and rows[seq - 1].hash != hash_:
                return {"ok": False, "checked": seq - 1, "first_bad_seq": seq, "reason": "head_mismatch"}
        else:
            return {"ok": False, "checked": len(rows), "first_bad_seq": None, "reason": "head_unreadable"}
    return {"ok": True, "checked": len(rows), "first_bad_seq": None}


def listing(db: Session, user_id: str, before_seq: int | None = None, limit: int = 50) -> list[PermissionEvent]:
    q = select(PermissionEvent).where(PermissionEvent.user_id == user_id)
    if before_seq is not None:
        q = q.where(PermissionEvent.seq < before_seq)
    return list(db.scalars(q.order_by(PermissionEvent.seq.desc()).limit(max(1, min(limit, 200)))).all())
