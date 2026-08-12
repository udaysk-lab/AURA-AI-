"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import preflight
from app.api import account, assistant, auth, chat, hub, intelligence, workspace
from app.config import settings
from app.db import init_db
from app.llm import get_provider
from app.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

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
    yield


app = FastAPI(
    title="AURA AI",
    description="AI executive assistant — email, calendar, tasks, memory, skills, automation.",
    version="1.0.0",
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
app.include_router(workspace.router)
app.include_router(intelligence.router)
app.include_router(hub.router)
app.include_router(account.router)
app.include_router(assistant.router)


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
    return {"status": "ok", "app": settings.app_name, "version": "1.0.0"}


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


@app.get("/api/health/preflight")
def preflight_report() -> JSONResponse:
    """Configuration diagnostics.

    Unauthenticated on purpose: someone who can't sign in because the deployment
    is misconfigured still needs to see why. It returns the *names* of missing
    settings and never a value, so it leaks nothing an attacker couldn't infer
    by watching the app fail.
    """
    report = preflight.run()
    return JSONResponse(
        status_code=200 if report.ready else 503, content=report.as_dict()
    )


@app.get("/")
def root() -> dict:
    return {
        "name": "AURA AI",
        "version": "1.0.0",
        "health": "/health",
        "readiness": "/health/ready",
        "setup": "/api/health/preflight",
        "docs": "/docs" if settings.environment != "production" else None,
    }
