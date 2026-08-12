"""Google Workspace integration (Gmail + Calendar).

Design rule: the rest of the app never calls Google directly. It reads and
writes the local tables; this module syncs them with Google when an account is
connected, and is a no-op when it isn't. That keeps every screen working in
demo mode and makes the agent's tools identical in both modes.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import parseaddr

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CalendarEvent, EmailMessage, OAuthAccount, User, utcnow
from app.security import decrypt_secret, encrypt_secret

log = logging.getLogger("aura.google")

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"


class GoogleNotConnected(Exception):
    pass


# ---------------------------------------------------------------------------
# Account + token handling
# ---------------------------------------------------------------------------


def get_account(db: Session, user_id: str) -> OAuthAccount | None:
    return db.scalars(
        select(OAuthAccount).where(
            OAuthAccount.user_id == user_id, OAuthAccount.provider == "google"
        )
    ).first()


def is_connected(db: Session, user_id: str) -> bool:
    acct = get_account(db, user_id)
    return bool(acct and (acct.refresh_token_enc or acct.access_token_enc))


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

# Google lets a user untick individual scopes on the consent screen, so
# "connected" is not the same as "can do the thing". Each capability lists the
# scopes that would satisfy it; the UI shows which ones actually came back.
CAPABILITIES: list[dict] = [
    {
        "key": "read_email",
        "label": "Read and triage email",
        "any_of": [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.readonly",
        ],
    },
    {
        "key": "send_email",
        "label": "Send and archive email",
        "any_of": [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    },
    {
        "key": "calendar",
        "label": "Read and manage your calendar",
        "any_of": [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events",
        ],
    },
]


def granted_capabilities(account: OAuthAccount | None) -> list[dict]:
    granted = set((account.scopes or "").split()) if account else set()
    return [
        {
            "key": cap["key"],
            "label": cap["label"],
            "granted": bool(granted & set(cap["any_of"])),
        }
        for cap in CAPABILITIES
    ]


def missing_capabilities(account: OAuthAccount | None) -> list[str]:
    return [c["label"] for c in granted_capabilities(account) if not c["granted"]]


def needs_reconnect(account: OAuthAccount | None) -> bool:
    """True when the connection exists but can't actually do the job.

    Two ways that happens: the user unticked scopes on the consent screen, or we
    only ever got an access token (Google returns a refresh token on first
    consent only, so a half-finished reconnect leaves you unable to refresh).
    """
    if not account:
        return False
    if not account.refresh_token_enc:
        return True
    return bool(missing_capabilities(account))


def _access_token(db: Session, account: OAuthAccount) -> str:
    """Return a valid access token, refreshing it if it has expired."""
    expires = account.expires_at
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    token = decrypt_secret(account.access_token_enc)
    if token and expires and expires > datetime.now(timezone.utc) + timedelta(seconds=60):
        return token

    refresh = decrypt_secret(account.refresh_token_enc)
    if not refresh:
        if token:
            return token
        raise GoogleNotConnected("No usable Google credentials; reconnect the account.")

    resp = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    account.access_token_enc = encrypt_secret(payload["access_token"])
    account.expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=int(payload.get("expires_in", 3600))
    )
    db.commit()
    return payload["access_token"]


def _headers(db: Session, user_id: str) -> dict:
    acct = get_account(db, user_id)
    if not acct:
        raise GoogleNotConnected("Google account not connected")
    return {"Authorization": f"Bearer {_access_token(db, acct)}"}


# ---------------------------------------------------------------------------
# OAuth flow helpers
# ---------------------------------------------------------------------------


def authorization_url(state: str) -> str:
    from urllib.parse import urlencode

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(settings.google_scope_list),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    resp = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_userinfo(access_token: str) -> dict:
    resp = httpx.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------


def _decode_part(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")


def _extract_body(payload: dict) -> str:
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    if body.get("data") and mime.startswith("text/"):
        return _decode_part(body["data"])
    for part in payload.get("parts", []) or []:
        text = _extract_body(part)
        if text:
            return text
    return ""


def sync_gmail(db: Session, user: User, max_results: int = 25) -> int:
    """Pull recent inbox messages into the local cache. Returns count synced."""
    if not is_connected(db, user.id):
        return 0
    headers = _headers(db, user.id)

    listing = httpx.get(
        f"{GMAIL_API}/messages",
        headers=headers,
        params={"maxResults": max_results, "labelIds": "INBOX"},
        timeout=30,
    )
    listing.raise_for_status()
    ids = [m["id"] for m in listing.json().get("messages", [])]

    synced = 0
    for msg_id in ids:
        exists = db.scalars(
            select(EmailMessage).where(
                EmailMessage.user_id == user.id, EmailMessage.external_id == msg_id
            )
        ).first()
        if exists:
            continue

        detail = httpx.get(
            f"{GMAIL_API}/messages/{msg_id}",
            headers=headers,
            params={"format": "full"},
            timeout=30,
        )
        detail.raise_for_status()
        data = detail.json()
        payload = data.get("payload", {})
        hdrs = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        name, addr = parseaddr(hdrs.get("from", ""))
        received = datetime.fromtimestamp(
            int(data.get("internalDate", 0)) / 1000, tz=timezone.utc
        )

        db.add(
            EmailMessage(
                user_id=user.id,
                external_id=msg_id,
                thread_id=data.get("threadId", ""),
                sender=addr,
                sender_name=name or addr,
                recipients=[hdrs.get("to", "")],
                subject=hdrs.get("subject", "(no subject)"),
                snippet=data.get("snippet", ""),
                body=_extract_body(payload)[:20000],
                received_at=received,
                is_read="UNREAD" not in data.get("labelIds", []),
                labels=data.get("labelIds", []),
            )
        )
        synced += 1

    db.commit()
    return synced


def send_email(db: Session, user: User, to: str, subject: str, body: str) -> dict:
    if not is_connected(db, user.id):
        return {"sent": False, "reason": "google_not_connected", "to": to, "subject": subject}

    message = MIMEText(body)
    message["to"] = to
    message["from"] = user.email
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    resp = httpx.post(
        f"{GMAIL_API}/messages/send",
        headers=_headers(db, user.id),
        json={"raw": raw},
        timeout=30,
    )
    resp.raise_for_status()
    return {"sent": True, "id": resp.json().get("id"), "to": to, "subject": subject}


def archive_email(db: Session, user: User, external_id: str) -> bool:
    if not external_id or not is_connected(db, user.id):
        return False
    resp = httpx.post(
        f"{GMAIL_API}/messages/{external_id}/modify",
        headers=_headers(db, user.id),
        json={"removeLabelIds": ["INBOX"]},
        timeout=30,
    )
    return resp.status_code < 300


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def _parse_dt(node: dict) -> datetime:
    raw = node.get("dateTime") or node.get("date")
    if not raw:
        return utcnow()
    if len(raw) == 10:  # all-day event: YYYY-MM-DD
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def sync_calendar(db: Session, user: User, days_ahead: int = 21) -> int:
    if not is_connected(db, user.id):
        return 0
    now = datetime.now(timezone.utc)
    resp = httpx.get(
        f"{CALENDAR_API}/calendars/primary/events",
        headers=_headers(db, user.id),
        params={
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(days=days_ahead)).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 100,
        },
        timeout=30,
    )
    resp.raise_for_status()

    synced = 0
    for item in resp.json().get("items", []):
        existing = db.scalars(
            select(CalendarEvent).where(
                CalendarEvent.user_id == user.id,
                CalendarEvent.external_id == item["id"],
            )
        ).first()
        row = existing or CalendarEvent(
            user_id=user.id, external_id=item["id"], source="google",
            start_at=utcnow(), end_at=utcnow(),
        )
        row.title = item.get("summary", "(no title)")
        row.description = item.get("description", "")
        row.location = item.get("location", "")
        row.start_at = _parse_dt(item.get("start", {}))
        row.end_at = _parse_dt(item.get("end", {}))
        row.attendees = [a.get("email", "") for a in item.get("attendees", []) or []]
        row.status = item.get("status", "confirmed")
        if not existing:
            db.add(row)
        synced += 1

    db.commit()
    return synced


def push_event(db: Session, user: User, event: CalendarEvent) -> str | None:
    """Create the event in Google. Returns the remote id, or None if offline."""
    if not is_connected(db, user.id):
        return None
    resp = httpx.post(
        f"{CALENDAR_API}/calendars/primary/events",
        headers=_headers(db, user.id),
        json={
            "summary": event.title,
            "description": event.description,
            "location": event.location,
            "start": {"dateTime": event.start_at.isoformat()},
            "end": {"dateTime": event.end_at.isoformat()},
            "attendees": [{"email": a} for a in (event.attendees or [])],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("id")


def delete_event_remote(db: Session, user: User, external_id: str) -> bool:
    if not external_id or not is_connected(db, user.id):
        return False
    resp = httpx.delete(
        f"{CALENDAR_API}/calendars/primary/events/{external_id}",
        headers=_headers(db, user.id),
        timeout=30,
    )
    return resp.status_code < 300


def sync_all(db: Session, user: User) -> dict:
    """Best-effort full sync. Never raises into the request path."""
    result = {"emails": 0, "events": 0, "connected": is_connected(db, user.id)}
    if not result["connected"]:
        return result

    errors: list[str] = []
    try:
        result["emails"] = sync_gmail(db, user)
    except Exception as exc:
        log.warning("Gmail sync failed for %s: %s", user.email, exc)
        result["email_error"] = str(exc)
        errors.append(f"Gmail: {exc}")
    try:
        result["events"] = sync_calendar(db, user)
    except Exception as exc:
        log.warning("Calendar sync failed for %s: %s", user.email, exc)
        result["calendar_error"] = str(exc)
        errors.append(f"Calendar: {exc}")

    # Record the outcome so Settings can show when it last worked, and why it
    # didn't. A connection that silently stopped syncing is worse than one that
    # says it's broken.
    account = get_account(db, user.id)
    if account:
        account.last_sync_at = utcnow()
        account.last_sync_emails = int(result["emails"])
        account.last_sync_events = int(result["events"])
        account.last_sync_error = "; ".join(errors)[:500]
        db.commit()

    result["errors"] = errors
    return result


def revoke_remote(db: Session, user_id: str) -> bool:
    """Tell Google to forget us, not just forget Google locally.

    Without this, deleting the row leaves a live grant on the user's Google
    account that they'd have to hunt down in their security settings.
    """
    account = get_account(db, user_id)
    if not account:
        return False
    token = decrypt_secret(account.refresh_token_enc) or decrypt_secret(
        account.access_token_enc
    )
    if not token:
        return False
    try:
        resp = httpx.post(
            "https://oauth2.googleapis.com/revoke",
            data={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        return resp.status_code < 400
    except Exception as exc:
        log.warning("Google revoke failed (local disconnect still applies): %s", exc)
        return False
