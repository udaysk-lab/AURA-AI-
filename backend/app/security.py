"""JWT issuing/verification and symmetric encryption for stored OAuth tokens."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from app.config import settings


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def create_access_token(user_id: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
        **(extra or {}),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

# bcrypt silently truncates at 72 bytes, so a longer password would make every
# suffix beyond it irrelevant. Reject rather than truncate, so nobody believes
# they have a 200-character password when they effectively have 72.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check. False for any account without a usable hash."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (ValueError, TypeError):
        # A malformed or truncated hash in the database must fail closed.
        return False


# ---------------------------------------------------------------------------
# Token encryption at rest
# ---------------------------------------------------------------------------


def _fernet() -> Fernet:
    key = settings.token_encryption_key
    if not key:
        # Deterministically derive a key from SECRET_KEY so local dev works
        # without extra setup. Production must set TOKEN_ENCRYPTION_KEY.
        digest = hashlib.sha256(settings.secret_key.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
