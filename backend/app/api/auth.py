"""Authentication: Google OAuth 2.0 plus a local dev login."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from jose import JWTError, jwt
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update

from app.config import settings
from app.deps import CurrentUser, DbSession, is_local_owner, local_os_user, local_owner_email
from app.version import __version__
from app.models import OAuthAccount, Schedule, User, UserSettings, utcnow
from app.schemas import (
    DemoLoginIn,
    IntegrationStatus,
    LocalLoginIn,
    LoginIn,
    PasswordForgotIn,
    PasswordResetIn,
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


LOCAL_INSTALL_ONLY_OWNER = (
    "This is AURA on someone's own computer, and it has one account: theirs. "
    "Sign in with “Open AURA on this computer”."
)


def _no_other_accounts_here() -> None:
    """A local install has exactly one account, its owner's, and one way in.

    Anything that can reach 127.0.0.1 — another user of a shared computer, any
    program running on it — could otherwise make an account of its own here
    and use the shell through it. The owner sign-in needs the key from the
    desktop app's link; signing up, passwords and demo sign-in need nothing.
    """
    if settings.local_mode:
        raise HTTPException(status_code=403, detail=LOCAL_INSTALL_ONLY_OWNER)


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
def auth_config(request: Request) -> dict:
    from app.middleware import is_loopback

    remote_client = settings.local_mode and not is_loopback(
        request.client.host if request.client else ""
    )
    return {
        # Passwords and demo sign-in make accounts, and a local install has
        # only its owner's. (Google can still be connected to it in Settings.)
        "google_enabled": settings.google_oauth_configured,
        "demo_login_enabled": settings.allow_demo_login and not settings.local_mode,
        "password_login_enabled": not settings.local_mode,
        # "Forgot password?" is only offered where a reset link can be sent.
        "password_reset_enabled": settings.smtp_configured and not settings.local_mode,
        "llm_provider": settings.resolved_provider(),
        # A local install is one person on one machine: the login screen offers
        # a single "open" button instead of asking them to invent an account.
        "local_mode": settings.local_mode,
        "device_control": settings.device_control_enabled,
        # The desktop app's sign-in needs the key from the link it opened.
        "local_login_key_required": bool(settings.local_mode and settings.aura_local_login_key),
        # A browser on another device, reaching this computer's AURA. Its
        # sign-in screen is the pairing form.
        "remote_client": remote_client,
        "remote_access": settings.remote_access_enabled,
        "version": __version__,
    }


LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


@router.post("/local", response_model=TokenOut)
def local_login(
    request: Request, db: DbSession, payload: LocalLoginIn | None = None
) -> TokenOut:
    """Sign in as this machine's owner. Local installs only, loopback only.

    On a local install there is exactly one person — whoever is sitting at the
    computer — and asking them to register an account to use their own machine
    is friction for nothing. So LOCAL_MODE grants a single owner account keyed
    to the OS user.

    Three things keep that honest. The request must come from loopback, so the
    door doesn't open to the network if someone binds AURA to 0.0.0.0. It
    requires LOCAL_MODE, which is off on every hosted deployment. And on the
    desktop app it requires the key from the link the app opened: every
    account on a shared computer can reach 127.0.0.1, but only the person who
    started AURA can read the settings file the key lives in.
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

    expected = settings.aura_local_login_key
    if expected:
        offered = (payload.key if payload else "") or ""
        if not offered or not hmac.compare_digest(offered.encode(), expected.encode()):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Open AURA from its app window (the \"Open AURA\" button) to sign in "
                    "on this computer."
                ),
            )

    os_user = local_os_user()
    email = local_owner_email()

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
        # The same first look every other sign-up gets: a populated workspace
        # to explore, removable in Settings once real accounts are connected.
        try:
            demo_service.seed_user(db, user)
        except Exception:
            log.exception("Seeding sample data for the local owner failed")
            db.rollback()

    return _issue(db, user)


@router.post("/register", response_model=TokenOut, status_code=201)
def register(payload: RegisterIn, db: DbSession) -> TokenOut:
    """Create an account with an email and password, and sign it in."""
    _no_other_accounts_here()
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
    _no_other_accounts_here()
    email = payload.email.lower().strip()
    user = db.scalars(select(User).where(User.email == email)).first()

    # One message for "no such user", "wrong password" and "Google-only
    # account", so a failed attempt reveals nothing about which it was.
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is disabled")

    return _issue(db, user)


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------
#
# The link carries a token signed with SECRET_KEY that names the account and a
# fingerprint of its current password hash. Setting a new password changes the
# hash, so a link works once and dies with the old password; it also expires
# after RESET_TTL. Nothing about it is stored.

_RESET_TYPE = "password_reset"
RESET_TTL = timedelta(minutes=30)
RESET_RESEND_AFTER = timedelta(minutes=2)
_FORGOT_REPLY = {
    "message": (
        "If an account uses that address, a link to set a new password is on its "
        "way. It works once, for 30 minutes."
    )
}


def _password_fingerprint(user: User) -> str:
    return hashlib.sha256(f"{user.id}:{user.password_hash}".encode()).hexdigest()[:24]


def issue_reset_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "typ": _RESET_TYPE,
            "sub": user.id,
            "pfp": _password_fingerprint(user),
            "iat": int(now.timestamp()),
            "exp": int((now + RESET_TTL).timestamp()),
        },
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )


def _reset_email(user: User, link: str) -> tuple[str, str]:
    subject = "Set a new password for AURA"
    body = (
        f"Hi {user.name or 'there'},\n\n"
        "Someone (hopefully you) asked to set a new password for your AURA account.\n"
        f"Open this link to choose one. It works once, for {int(RESET_TTL.total_seconds() // 60)} minutes:\n\n"
        f"{link}\n\n"
        "If you didn't ask for this, ignore this email — your password stays as it is.\n"
    )
    return subject, body


@router.post("/password/forgot")
def password_forgot(payload: PasswordForgotIn, db: DbSession) -> dict:
    """Email a link to set a new password.

    The reply is the same whether or not the address has an account, so the
    form can't be used to find out who uses AURA.
    """
    from app.services import mailer

    if settings.local_mode or not settings.smtp_configured:
        raise HTTPException(
            status_code=501,
            detail=(
                "Password reset by email isn't set up on this server. "
                "Ask whoever runs it to configure SMTP_HOST and SMTP_FROM."
            ),
        )

    email = payload.email.lower().strip()
    user = db.scalars(select(User).where(User.email == email)).first()
    if not user or not user.is_active or user.is_demo:
        return _FORGOT_REPLY

    now = datetime.now(timezone.utc)
    last = user.password_reset_sent_at
    if last is not None:
        last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        if now - last < RESET_RESEND_AFTER:
            return _FORGOT_REPLY  # one email per address every couple of minutes

    link = f"{settings.frontend_url.rstrip('/')}/reset-password#token={issue_reset_token(user)}"
    subject, body = _reset_email(user, link)
    try:
        mailer.send_system(user.email, subject, body)
    except Exception:
        log.exception("Couldn't send a password reset email for %s", user.id)
        raise HTTPException(
            status_code=503,
            detail="The reset email couldn't be sent just now. Please try again in a few minutes.",
        )
    user.password_reset_sent_at = now
    db.commit()
    log.info("Password reset link sent for %s", user.id)
    return _FORGOT_REPLY


@router.post("/password/reset", response_model=TokenOut)
def password_reset(payload: PasswordResetIn, db: DbSession) -> TokenOut:
    """Set a new password from an emailed link, and sign in."""
    _no_other_accounts_here()
    invalid = HTTPException(
        status_code=400,
        detail="That link has expired or has already been used. Ask for a new one.",
    )
    try:
        claims = jwt.decode(
            payload.token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        raise invalid
    if claims.get("typ") != _RESET_TYPE:
        raise invalid
    user = db.get(User, str(claims.get("sub") or ""))
    if not user or not user.is_active:
        raise invalid
    if not hmac.compare_digest(str(claims.get("pfp") or ""), _password_fingerprint(user)):
        raise invalid

    user.password_hash = hash_password(payload.password)
    user.password_reset_sent_at = None
    db.commit()
    log.info("Password reset for %s", user.id)
    return _issue(db, user)


@router.post("/demo", response_model=TokenOut)
def demo_login(payload: DemoLoginIn, db: DbSession) -> TokenOut:
    """Password-free local login. Disable with ALLOW_DEMO_LOGIN=false."""
    _no_other_accounts_here()
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
        # A session token, not a reset link or another signed token that
        # happens to name an account.
        if payload and payload.get("sub") and not payload.get("typ") and db.get(User, payload["sub"]):
            user_id = payload["sub"]

    if settings.local_mode:
        # One account here, the owner's: Google can be connected to it, but it
        # can't sign anyone in or make a second account.
        owner = db.get(User, user_id) if user_id else None
        if owner is None or not is_local_owner(owner):
            raise HTTPException(
                status_code=403,
                detail=(
                    "On your own computer, sign in with “Open AURA on this computer”, "
                    "then connect Google from Settings."
                ),
            )

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
    if settings.local_mode and mode == "signin":
        # google_start never begins one here; a state from elsewhere can't either.
        return _fail(mode, "local_install")

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
        if not user or (settings.local_mode and not is_local_owner(user)):
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
