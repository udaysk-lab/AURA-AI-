"""Authentication: Google OAuth 2.0 plus a local dev login."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.deps import CurrentUser, DbSession
from app.models import OAuthAccount, User, UserSettings, utcnow
from app.schemas import (
    DemoLoginIn,
    IntegrationStatus,
    LoginIn,
    RegisterIn,
    SyncResultOut,
    TokenOut,
    UserOut,
)
from app.security import (
    create_access_token,
    decode_access_token,
    encrypt_secret,
    hash_password,
    verify_password,
)
from app import plugins as plugin_registry
from app.agent import skills as skill_registry
from app.services import channels as channel_service
from app.services import demo as demo_service
from app.services import google as google_service
from app.services import identity as identity_service

log = logging.getLogger("aura.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

# Short-lived CSRF state for the OAuth round trip, holding which user started it
# and whether they were signing in or connecting. In-process, so it only works
# with one worker — move to Redis before scaling out.
_oauth_state: dict[str, dict] = {}


def _issue(db, user: User) -> TokenOut:
    user.last_login_at = utcnow()
    existing = db.scalars(
        select(UserSettings).where(UserSettings.user_id == user.id)
    ).first()
    if not existing:
        db.add(UserSettings(user_id=user.id))
    db.commit()
    # Every account gets an assistant, the core plugins, their skills, and the
    # web channel from first login.
    identity_service.get_or_create(db, user)
    plugin_registry.ensure_core(db, user.id)
    skill_registry.ensure_user_skills(db, user.id)
    channel_service.ensure_web(db, user.id)
    db.refresh(user)
    return TokenOut(
        access_token=create_access_token(user.id), user=UserOut.model_validate(user)
    )


@router.get("/config")
def auth_config() -> dict:
    return {
        "google_enabled": settings.google_oauth_configured,
        "demo_login_enabled": settings.allow_demo_login,
        "password_login_enabled": True,
        "llm_provider": settings.resolved_provider(),
    }


@router.post("/register", response_model=TokenOut, status_code=201)
def register(payload: RegisterIn, db: DbSession) -> TokenOut:
    """Create an account with an email and password, and sign it in."""
    email = payload.email.lower().strip()
    existing = db.scalars(select(User).where(User.email == email)).first()
    if existing:
        # Deliberately explicit. Email enumeration is already possible here by
        # design — this is a sign-up form, and a vague error would just make
        # people retry the same address. The protection that matters is the
        # rate limit on this route, not pretending the address is free.
        raise HTTPException(status_code=409, detail="That email is already registered")

    user = User(
        email=email,
        name=payload.name.strip() or email.split("@")[0],
        timezone="UTC",
        password_hash=hash_password(payload.password),
        is_demo=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserSettings(user_id=user.id))
    db.commit()
    log.info("registered user %s", user.id)
    return _issue(db, user)


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: DbSession) -> TokenOut:
    """Email and password sign-in."""
    email = payload.email.lower().strip()
    user = db.scalars(select(User).where(User.email == email)).first()

    # One message for "no such user", "wrong password" and "Google-only
    # account", so a failed attempt reveals nothing about which it was.
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is disabled")

    return _issue(db, user)


@router.post("/demo", response_model=TokenOut)
def demo_login(payload: DemoLoginIn, db: DbSession) -> TokenOut:
    """Password-free local login. Disable with ALLOW_DEMO_LOGIN=false."""
    if not settings.allow_demo_login:
        raise HTTPException(status_code=403, detail="Demo login is disabled")

    email = payload.email.lower()
    user = db.scalars(select(User).where(User.email == email)).first()
    if not user:
        user = User(email=email, name=payload.name, timezone="UTC", is_demo=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        db.add(UserSettings(user_id=user.id))
        db.commit()
        demo_service.seed_user(db, user)
    return _issue(db, user)


def _prune_states() -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - 600
    for key in [k for k, v in _oauth_state.items() if v.get("ts", 0) < cutoff]:
        _oauth_state.pop(key, None)


def _fail(mode: str, reason: str) -> RedirectResponse:
    """Send the user back where they came from, with something actionable."""
    target = "/settings" if mode == "connect" else "/login"
    return RedirectResponse(f"{settings.frontend_url}{target}?google=error&reason={reason}")


@router.get("/google/start")
def google_start(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """Begin the OAuth round trip.

    Two distinct flows share this endpoint, and which one you get depends on
    whether you arrive with a session:

      * no session  → "signin": the Google account identifies you
      * session     → "connect": the Google account is attached to the account
                      you are *already* signed in as

    Binding the mode and the user id to the CSRF state is what makes the second
    flow safe. Without it the callback has to guess who the tokens belong to,
    and its only clue is the Google email — so connecting a personal Gmail while
    signed in as someone else silently switches you to a different account.
    """
    if not settings.google_oauth_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Google OAuth isn't configured. Set GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET, then restart the backend."
            ),
        )

    user_id: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        payload = decode_access_token(authorization[7:])
        if payload and payload.get("sub") and db.get(User, payload["sub"]):
            user_id = payload["sub"]

    mode = "connect" if user_id else "signin"
    state = secrets.token_urlsafe(24)
    _oauth_state[state] = {
        "ts": datetime.now(timezone.utc).timestamp(),
        "user_id": user_id,
        "mode": mode,
    }
    _prune_states()

    return {
        "authorization_url": google_service.authorization_url(state),
        "state": state,
        "mode": mode,
    }


@router.get("/google/callback")
def google_callback(
    db: DbSession,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
) -> RedirectResponse:
    entry = _oauth_state.pop(state, None) if state else None
    mode = (entry or {}).get("mode", "signin")

    if error:
        # access_denied is the user clicking Cancel — not worth an alarming message.
        return _fail(mode, "denied" if error == "access_denied" else error)
    if not code:
        return _fail(mode, "no_code")
    if entry is None:
        # Expired (10 min), already used, or forged.
        return _fail(mode, "bad_state")

    try:
        tokens = google_service.exchange_code(code)
        profile = google_service.fetch_userinfo(tokens["access_token"])
    except Exception:
        log.exception("Google OAuth exchange failed")
        return _fail(mode, "exchange_failed")

    google_email = (profile.get("email") or "").lower()
    if not google_email:
        return _fail(mode, "no_email")

    # ---- Resolve which AURA account these tokens belong to -----------------
    is_new = False
    if mode == "connect":
        user = db.get(User, entry.get("user_id") or "")
        if not user:
            return _fail("connect", "session_expired")

        # One mailbox, one account. Otherwise two AURA users would both be
        # reading and sending from the same inbox with separate permissions.
        clash = db.scalars(
            select(OAuthAccount).where(
                OAuthAccount.provider == "google",
                OAuthAccount.email == google_email,
                OAuthAccount.user_id != user.id,
            )
        ).first()
        if clash:
            return _fail("connect", "already_linked")
    else:
        user = db.scalars(select(User).where(User.email == google_email)).first()
        is_new = user is None
        if is_new:
            user = User(
                email=google_email,
                name=profile.get("name", ""),
                avatar_url=profile.get("picture", ""),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            db.add(UserSettings(user_id=user.id))
            db.commit()

    # ---- Store the grant ---------------------------------------------------
    account = google_service.get_account(db, user.id)
    if account is None:
        account = OAuthAccount(user_id=user.id, provider="google")
        db.add(account)

    account.provider_account_id = profile.get("id", "")
    account.email = google_email
    account.access_token_enc = encrypt_secret(tokens["access_token"])
    # Google only returns a refresh token on first consent. Never overwrite a
    # good one with nothing — that's how a working connection loses the ability
    # to refresh and starts failing an hour later.
    if tokens.get("refresh_token"):
        account.refresh_token_enc = encrypt_secret(tokens["refresh_token"])
    account.expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=int(tokens.get("expires_in", 3600))
    )
    account.scopes = tokens.get("scope", "")
    account.last_sync_error = ""
    db.commit()

    if not user.avatar_url and profile.get("picture"):
        user.avatar_url = profile["picture"]
    if not user.name and profile.get("name"):
        user.name = profile["name"]
    db.commit()

    identity_service.get_or_create(db, user)
    plugin_registry.ensure_core(db, user.id)
    skill_registry.ensure_user_skills(db, user.id)
    channel_service.ensure_web(db, user.id)
    if is_new:
        demo_service.seed_user(db, user)

    try:
        google_service.sync_all(db, user)
    except Exception as exc:
        log.warning("Initial Google sync failed: %s", exc)

    # ---- Send them back ----------------------------------------------------
    if mode == "connect":
        # Already signed in; don't reissue a token or touch the session.
        query = "google=connected"
        if google_service.missing_capabilities(account):
            query = "google=partial"
        return RedirectResponse(f"{settings.frontend_url}/settings?{query}")

    token = create_access_token(user.id)
    return RedirectResponse(f"{settings.frontend_url}/auth/callback#token={token}")


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/integrations", response_model=list[IntegrationStatus])
def integrations(user: CurrentUser, db: DbSession) -> list[IntegrationStatus]:
    account = google_service.get_account(db, user.id)
    connected = bool(account and (account.refresh_token_enc or account.access_token_enc))

    return [
        IntegrationStatus(
            provider="google",
            connected=connected,
            available=settings.google_oauth_configured,
            email=account.email if account else "",
            scopes=(account.scopes.split() if account and account.scopes else []),
            capabilities=google_service.granted_capabilities(account if connected else None),
            needs_reconnect=google_service.needs_reconnect(account) if connected else False,
            last_sync_at=account.last_sync_at if account else None,
            last_sync_emails=account.last_sync_emails if account else 0,
            last_sync_events=account.last_sync_events if account else 0,
            last_sync_error=account.last_sync_error if account else "",
            connected_at=account.created_at if account else None,
        ),
        IntegrationStatus(provider="microsoft", connected=False, available=False),
        IntegrationStatus(provider="slack", connected=False, available=False),
        IntegrationStatus(provider="notion", connected=False, available=False),
    ]


@router.post("/integrations/google/sync", response_model=SyncResultOut)
def sync_google(user: CurrentUser, db: DbSession) -> SyncResultOut:
    """Pull Gmail and Calendar now, rather than waiting for the heartbeat."""
    if not google_service.is_connected(db, user.id):
        raise HTTPException(status_code=400, detail="Google isn't connected.")

    result = google_service.sync_all(db, user)
    errors = result.get("errors", [])
    if errors:
        message = f"Synced with problems: {'; '.join(errors)}"
    else:
        message = (
            f"Synced {result['emails']} email(s) and {result['events']} event(s)."
            if result["emails"] or result["events"]
            else "Already up to date."
        )
    return SyncResultOut(
        connected=True,
        emails=result["emails"],
        events=result["events"],
        errors=errors,
        message=message,
    )


@router.delete("/integrations/google")
def disconnect_google(
    user: CurrentUser,
    db: DbSession,
    revoke: bool = Query(True, description="Also revoke the grant at Google"),
) -> dict:
    """Disconnect, and by default tell Google to forget us too.

    Deleting only the local row would leave a live grant sitting in the user's
    Google security settings that they'd have to find and remove by hand.
    """
    account = google_service.get_account(db, user.id)
    if not account:
        return {"message": "Google wasn't connected.", "revoked": False}

    revoked = google_service.revoke_remote(db, user.id) if revoke else False
    db.delete(account)
    db.commit()

    return {
        "message": (
            "Google disconnected and access revoked."
            if revoked
            else "Google disconnected. Remove AURA at myaccount.google.com if you also "
            "want to revoke the grant."
        ),
        "revoked": revoked,
    }
