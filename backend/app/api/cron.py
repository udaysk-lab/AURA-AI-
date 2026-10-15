"""Background work on a platform with no long-running process.

The hosted site runs on Vercel, where functions only run while answering a
request. worker.py's timer has nowhere to live there, so the heartbeat,
schedules, automations and memory compaction never ran on the live site.
Vercel's cron calls this instead (see `crons` in vercel.json).

Two halves:

* Every call touches the database, so a Free-plan Supabase project never idles
  into an automatic pause — what /api/health/keepalive does.
* With CRON_SECRET set and the request carrying it, it also runs one pass of
  the worker. Vercel adds "Authorization: Bearer $CRON_SECRET" to its cron
  calls by itself once the variable exists, so setting it is the whole setup.

Without the secret nobody else can make AURA do work — and spend model tokens —
by calling the URL; they only get the keep-alive.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings

log = logging.getLogger("aura.cron")
router = APIRouter(prefix="/api/cron", tags=["cron"])

# Background work is for people using AURA. Accounts nobody has signed into
# for a month are left alone rather than triaged forever.
ACTIVE_WINDOW = timedelta(days=30)
NO_STORE = {"Cache-Control": "no-store"}


def _bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


@router.get("/tick")
def cron_tick(authorization: Annotated[str | None, Header()] = None) -> JSONResponse:
    from app.db import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT id FROM users LIMIT 1"))
    except Exception as exc:
        log.error("Cron tick could not reach the database: %s", str(exc).splitlines()[0][:200])
        return JSONResponse(status_code=503, content={"status": "unreachable"}, headers=NO_STORE)

    if not settings.cron_secret:
        return JSONResponse(
            content={"status": "ok", "worker": "off: set CRON_SECRET to run background work here"},
            headers=NO_STORE,
        )

    offered = _bearer(authorization)
    if not offered:
        return JSONResponse(content={"status": "ok", "worker": "skipped"}, headers=NO_STORE)
    if not hmac.compare_digest(offered.encode(), settings.cron_secret.encode()):
        log.warning("Cron tick with a wrong secret")
        return JSONResponse(status_code=401, content={"detail": "Wrong cron secret."}, headers=NO_STORE)

    import worker

    started = datetime.now(timezone.utc)
    summary = worker.tick(
        compact=True,
        active_since=started - ACTIVE_WINDOW,
        budget_seconds=max(1, settings.cron_budget_seconds),
    )
    summary["seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 2)
    log.info("Cron tick: %s", summary)
    return JSONResponse(
        status_code=200 if summary.get("ok") else 500,
        content={"status": "ok" if summary.get("ok") else "error", "worker": "ran", **summary},
        headers=NO_STORE,
    )
