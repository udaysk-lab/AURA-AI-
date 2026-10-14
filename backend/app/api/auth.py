"""Authentication: Google OAuth 2.0 plus a local dev login."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from jose import JWTError, jwt
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update

from app.config import settings
from app.deps import CurrentUser, DbSession
from app.models import OAuthAccount, Schedule, User, UserSettings, utcnow
from app.schemas import (
    DemoLoginIn,
    IntegrationStatus,
    LoginIn,
    ProfilePatch,
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
from app import timeutil
from app.agent import skills as skill_registry
from app.services import channels as channel_service
from app.services import demo as demo_service
from app.services import google as google_service
from app.services import identity as identity_service

log = logging.getLogger("aura.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

# CSRF state for the OAuth round trip: who started it, whether they were signing
# in or connecting, and their zone. It used to live in a dict in this process,
# which only works with one worker. On Vercel the callback usually lands on a
# different instance from the one that started the flow, so Google sign-in
# failed with bad_state more often than not. It is now a short-lived token
# signed with SECRET_KEY, which any instance can check.
_STATE_TYPE = "google_oauth_state"
_STATE_TTL = timedelta(minutes=10)
# Best effort against a state being replayed on the same instance. Google's
# code is single-use anyway; this just refuses the second attempt early.
_used_states: dict[str, float] = {}


def _issue_state(user_id: str | None, mode: str, tz: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "typ": _STATE_TYPE,
            "uid": user_id,
            "mode": mode,
            "tz": tz,
            "nonce": secrets.token_urlsafe(12),
            "iat": int(now.timestamp()),
            "exp": int((now + _STATE_TTL).timestamp()),
        },
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )


def _read_state(state: str) -> dict | None:
    """The flow a state belongs to, or None if it's forged, expired or reused.

    No "sub" claim, so a state can never pass for a session token, and a
    session token has no "typ", so it can never pass for a state.
    """
    try:
        claims = jwt.decode(state, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    if claims.get("typ") != _STATE_TYPE or claims.get("mode") not in ("signin", "connect"):
        return None
    now = datetime.now(timezone.utc).timestamp()
    for nonce in [n for n, exp in _used_states.items() if exp < now]:
        _used_states.pop(nonce, None)
    nonce = str(claims.get("nonce") or "")
    if not nonce or nonce in _used_states:
        return None
    _used_states[nonce] = float(claims.get("exp") or now)
    return {"user_id": claims.get("uid"), "mode": claims["mode"], "tz": claims.get("tz") or ""}


def _zone_or_utc(name: str | None) -> str:
    """Keep the browser's zone if it's a real IANA name, otherwise UTC."""
    name = (name or "").strip()
    return name if name and timeutil.is_valid_zone(name) else "UTC"


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
        # A local install is one person on one machine: the login screen offers
        # a single "open" button instead of asking them to invent an account.
        "local_mode": settings.local_mode,
        "device_control": settings.device_control_enabled,
    }


LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


@router.post("/local", response_model=TokenOut)
def local_login(request: Request, db: DbSession) -> TokenOut:
    """Sign in as this machine's owner. Local installs only, loopback only.

    On a local install there is exactly one person — whoever is sitting at the
    computer — and asking them to register an account to use their own machine
    is friction for nothing. So LOCAL_MODE grants a single owner account keyed
    to the OS user.

    Two things keep that honest. The request must come from loopback, so the
    door doesn't open to the network if someone binds AURA to 0.0.0.0. And it
    requires LOCAL_MODE, which is off on every hosted deployment. Anyone who
    can reach loopback on this machine can already run commands as this user,
    so this grants no authority they didn't have.
    """
    if not settings.local_mode:
        raise HTTPException(
            status_code=403,
            detail="Local sign-in is only available when AURA runs in local mode.",
        )

    client = request.client.host if request.client else ""
    if client not in LOOPBACK:
        log.warning("Rejected local sign-in from %s (not loopback)", client)
        raise HTTPException(
            status_code=403,
            detail=(
                "Local sign-in only works from this computer. AURA appears to be "
                "listening on the network — bind it to 127.0.0.1, or sign in with an "
                "email and password."
            ),
        )

    import getpass

    try:
        os_user = getpass.getuser()
    except Exception:
        os_user = "owner"
    email = f"{os_user.lower().replace(' ', '-')}@localhost"

    user = db.scalars(select(User).where(User.email == email)).first()
    if not user:
        user = User(
            email=email,
            name=os_user.replace(".", " ").replace("-", " ").title(),
            timezone=_zone_or_utc(timeutil.system_zone_name()),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.add(UserSettings(user_id=user.id))
        db.commit()
        log.info("Created the local owner account for %s", email)

    return _issue(db, user)


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
        timezone=_zone_or_utc(payload.timezone),
        password_hash=hash_password(payload.password),
        is_demo=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserSettings(user_id=user.id))
    db.commit()
    log.info("registered user %s", user.id)
    token = _issue(db, user)
    # The landing page promises that a new account "runs on realistic sample
    # data out of the box", and the Google sign-up path already seeds it. This
    # path didn't, so a password sign-up — the only kind the live site offers —
    # landed in an empty app ("0 unread of 0"). It can be removed from
    # Settings, or replaced by connecting Google.
    try:
        demo_service.seed_user(db, user)
    except Exception:  # sample data is a nicety; never fail a sign-up over it
        log.exception("Seeding sample data failed for %s", user.id)
        db.rollback()
    return token


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
        user = User(
            email=email, name=payload.name,
            timezone=_zone_or_utc(payload.timezone), is_demo=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.add(UserSettings(user_id=user.id))
        db.commit()
        demo_service.seed_user(db, user)
    return _issue(db, user)


def _fail(mode: str, reason: str) -> RedirectResponse:
    """Send the user back where they came from, with something actionable."""
    target = "/settings" if mode == "connect" else "/login"
    return RedirectResponse(f"{settings.frontend_url}{target}?google=error&reason={reason}")


@router.get("/google/start")
def google_start(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
    tz: str = Query(default="", max_length=64),
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
    # The browser's zone rides along, for an account this sign-in creates.
    state = _issue_state(user_id, mode, tz if isinstance(tz, str) else "")

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
    entry = _read_state(state) if state else None
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
                timezone=_zone_or_utc((entry or {}).get("tz")),
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


@router.patch("/me", response_model=UserOut)
def update_me(payload: ProfilePatch, user: CurrentUser, db: DbSession) -> UserOut:
    """Update the signed-in user's display name and time zone.

    The web app sends the browser's zone when the account still has the UTC
    default, so "today", "tomorrow at 5" and working hours mean the user's own.
    """
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Name can't be empty.")
        user.name = name
    if payload.timezone is not None:
        tz_name = payload.timezone.strip()
        if not timeutil.is_valid_zone(tz_name):
            raise HTTPException(
                status_code=422,
                detail=f"'{tz_name}' isn't a time zone I recognise. Use a name like Asia/Kolkata.",
            )
        new_zone = "UTC" if tz_name.upper() == "UTC" else tz_name
        old_zone = user.timezone or "UTC"
        if new_zone != old_zone:
            # Schedules made on the old zone followed the account; they follow
            # it here too, or "every weekday at 7:30am" stays 7:30 somewhere
            # else — the sample one included, seeded before the zone was known.
            db.execute(
                update(Schedule)
                .where(Schedule.user_id == user.id, Schedule.timezone == old_zone)
                .values(timezone=new_zone)
            )
        user.timezone = new_zone
    db.commit()
    db.refresh(user)
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
