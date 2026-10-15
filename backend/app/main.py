"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app import preflight, web
from app.api import (
    account,
    assistant,
    auth,
    chat,
    cron,
    device,
    hub,
    intelligence,
    local,
    remote,
    workspace,
)
from app.config import settings
from app.db import init_db
from app.llm import get_provider
from app.middleware import (
    LocalAccessMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.version import __version__

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("aura")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()

    report = preflight.log_report(log)
    # In production a blocking failure means an insecure or non-functional
    # deployment. Refusing to start is kinder than serving it.
    if settings.environment == "production" and not report.ready:
        raise RuntimeError(
            "Refusing to start: "
            + "; ".join(f"{c.label} — {c.fix or c.detail}" for c in report.failures)
        )

    log.info(
        "%s listening | db=%s | provider=%s",
        settings.app_name,
        "sqlite" if settings.is_sqlite else "postgres",
        get_provider().name,
    )

    if settings.device_control_enabled:
        from app.device.runtime import capabilities

        caps = capabilities()
        log.info(
            "Device control ON | %s %s | shell=%s | screen=%s | roots=%s",
            caps["os"], caps["arch"], caps["shell"]["kind"],
            "yes" if caps["screen_control"] else "no",
            ", ".join(settings.device_allowed_root_list),
        )

    # On a local install one launch should do everything, so the background
    # loops (heartbeat, schedules, automations, compaction) run on a thread in
    # this process instead of needing a second terminal. A server sets
    # RUN_EMBEDDED_WORKER=off and runs worker.py separately.
    stop_worker = None
    if settings.embedded_worker_enabled:
        import worker

        stop_worker = worker.start_embedded()

    try:
        yield
    finally:
        if stop_worker is not None:
            stop_worker()


app = FastAPI(
    title="AURA AI",
    description="AI executive assistant — email, calendar, tasks, memory, skills, automation.",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.environment != "production" else None,
)

# Order matters: outermost first. Request context wraps everything so even a
# rate-limited response carries a request ID.
app.add_middleware(RequestContextMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
# Local installs only: answer to this computer by its own name, and to paired
# devices. Does nothing on a hosted deployment.
app.add_middleware(LocalAccessMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # Local dev only — see config.cors_origin_regex. None in every other
    # environment, so the explicit allowlist above is the whole policy.
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(device.router)
app.include_router(workspace.router)
app.include_router(intelligence.router)
app.include_router(hub.router)
app.include_router(account.router)
app.include_router(assistant.router)
app.include_router(cron.router)
app.include_router(local.router)
app.include_router(remote.router)


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


# Shown when the database can't be reached. The sign-in form displays it as is,
# so it has to tell someone at that form what they need to know: the password
# isn't the problem, and trying again later is the fix.
DATABASE_UNAVAILABLE = (
    "AURA can't reach its database right now, so this couldn't be completed. "
    "The problem is on the server, not with your account. "
    "Please try again in a few minutes."
)


@app.exception_handler(OperationalError)
async def database_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
    """A database that can't be reached is a 503, not a mystery 500.

    Before this, an outage reached the sign-in form as "Internal error". That
    reads like a bug in the app, or a wrong password, when the real cause was
    that the database was down: the Supabase project had auto-paused after a
    week without traffic. A 503 with a plain explanation points at the actual
    problem, and Retry-After tells clients that waiting is the remedy.
    """
    request_id = getattr(request.state, "request_id", "-")
    cause = exc.orig or exc
    # One line rather than a traceback. During an outage every request lands
    # here, and the driver's message (for example "tenant/user ... not found"
    # or "connection refused") is the part that says what went wrong.
    log.error(
        "[%s] Database unavailable on %s %s: %s",
        request_id, request.method, request.url.path, _first_line(cause),
    )
    detail = DATABASE_UNAVAILABLE
    if settings.debug:
        detail = f"{DATABASE_UNAVAILABLE} ({_first_line(cause)})"
    return JSONResponse(
        status_code=503,
        content={"detail": detail, "request_id": request_id},
        headers={"X-Request-ID": request_id, "Retry-After": "60"},
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    log.exception("[%s] Unhandled error on %s %s", request_id, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            # Internal messages leak schema and paths; only expose them in dev.
            "detail": str(exc) if settings.debug else "Internal error",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


@app.get("/health")
def health() -> dict:
    """Liveness. Deliberately cheap and unauthenticated — no DB call."""
    return {"status": "ok", "app": settings.app_name, "version": __version__}


@app.get("/health/ready")
def ready() -> JSONResponse:
    """Readiness. Touches the database, so a load balancer can drain a bad pod."""
    from sqlalchemy import text

    from app.db import engine

    checks: dict[str, str] = {}
    healthy = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
        healthy = False

    checks["provider"] = get_provider().name
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "degraded", "checks": checks},
    )


@app.get("/api/health/keepalive")
def keepalive() -> JSONResponse:
    """Give the database a little real traffic every day.

    Supabase pauses a Free-plan project after a week without database activity.
    A paused project's hostnames stop resolving and its pooler answers
    "tenant/user not found", so everything that needs the database fails, sign-in
    first. That is how the live site went down in September 2026, a few weeks
    after it was last used. Restoring the project brings it back with its data,
    but only by hand.

    The `crons` in vercel.json call this three times a day. It reads a real
    table rather than running SELECT 1, so the traffic is an ordinary query
    against user data, not a no-op. It returns nothing about that data, so it is
    safe to leave unauthenticated, like /health/ready.
    """
    from sqlalchemy import text

    from app.db import engine

    no_store = {"Cache-Control": "no-store"}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT id FROM users LIMIT 1"))
    except Exception as exc:
        log.error("Keep-alive could not reach the database: %s", _first_line(exc))
        return JSONResponse(
            status_code=503, content={"status": "unreachable"}, headers=no_store
        )
    return JSONResponse(content={"status": "ok"}, headers=no_store)


@app.get("/api/health/preflight")
def preflight_report() -> JSONResponse:
    """Configuration diagnostics.

    Unauthenticated on purpose: someone who can't sign in because the deployment
    is misconfigured still needs to see why. It returns the *names* of missing
    settings and never a value, so it leaks nothing an attacker couldn't infer
    by watching the app fail.
    """
    # This one also opens a real database connection: the Setup screen is
    # where someone looks when sign-in fails, and "Postgres" said nothing
    # about whether it could actually be reached.
    report = preflight.run(probe_database=True)
    return JSONResponse(
        status_code=200 if report.ready else 503,
        content=report.as_dict(),
        headers={"Cache-Control": "no-store"},
    )


# The desktop app serves the web app from this same address, which owns "/".
# Everywhere else "/" describes the API. Registered last: the web app's routes
# catch every path the API doesn't claim.
WEB_APP_SERVED = bool(settings.aura_web_dir) and web.install(app, settings.aura_web_dir)

if not WEB_APP_SERVED:

    @app.get("/")
    def root() -> dict:
        return {
            "name": "AURA AI",
            "version": __version__,
            "health": "/health",
            "readiness": "/health/ready",
            "setup": "/api/health/preflight",
            "docs": "/docs" if settings.environment != "production" else None,
        }
