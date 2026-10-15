"""Behaviour when the database is unreachable.

In September 2026 the live site's Supabase project auto-paused after a week
without traffic. Every request that touched the database raised
sqlalchemy.exc.OperationalError, and the sign-in form showed "Internal error",
which looked like a bug in the app or a wrong password.

These tests pin down two things:
  * an unreachable database surfaces as a 503 with a message that says so;
  * /api/health/keepalive, which the vercel.json crons call so the project never
    idles long enough to pause, reports honestly either way.

"Unreachable" here is real, not mocked: a Postgres engine pointed at port 1 on
localhost, where nothing listens, so psycopg raises the same exception class a
paused project produces.

Run from backend/:  pytest -q tests/test_database_outage.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Must match test_smoke.py exactly — see the note in test_password_auth.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import app.db as db_module  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import DATABASE_UNAVAILABLE, app  # noqa: E402

# Nothing listens on port 1, so the connection is refused immediately.
DEAD_URL = "postgresql+psycopg://aura:aura@127.0.0.1:1/aura"

# A separate rate-limit bucket, so these requests never count against the
# anonymous per-IP limit that the other test modules share.
HEADERS = {"X-Forwarded-For": "203.0.113.7"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def dead_engine():
    engine = create_engine(
        DEAD_URL, poolclass=NullPool, connect_args={"connect_timeout": 3}
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def database_down(dead_engine):
    """Route every request's session to the unreachable database."""
    DeadSession = sessionmaker(bind=dead_engine, autoflush=False)

    def _dead_db():
        db = DeadSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _dead_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# An outage is a 503 that says what happened
# ---------------------------------------------------------------------------


def test_login_during_an_outage_is_503_not_internal_error(client, database_down):
    resp = client.post(
        "/api/auth/login",
        json={"email": "someone@example.com", "password": "correct-horse-battery"},
        headers=HEADERS,
    )
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["detail"].startswith(DATABASE_UNAVAILABLE)
    assert "Internal error" not in resp.text
    # Clients are told that waiting is the fix, and the request can be traced.
    assert resp.headers["Retry-After"] == "60"
    assert body["request_id"] == resp.headers["X-Request-ID"]


def test_outage_message_does_not_blame_the_password():
    # The sign-in form shows this verbatim. Someone reading it there must not
    # conclude their password is wrong and start resetting it.
    lowered = DATABASE_UNAVAILABLE.lower()
    assert "not with your account" in lowered
    assert "try again" in lowered
    assert "internal error" not in lowered


def test_signup_during_an_outage_is_503(client, database_down):
    resp = client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": "long-enough-pw", "name": "New"},
        headers=HEADERS,
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"].startswith(DATABASE_UNAVAILABLE)


def test_other_errors_are_still_a_generic_500(client):
    """Only database outages get the 503. Everything else keeps the old
    handler, which hides internals outside debug mode."""

    @app.get("/__test_boom")
    def boom():  # pragma: no cover - body replaced by the raise
        raise ValueError("secret internals")

    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/__test_boom", headers=HEADERS)
        assert resp.status_code == 500
        assert DATABASE_UNAVAILABLE not in resp.text
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", "") != "/__test_boom"
        ]


# ---------------------------------------------------------------------------
# Keep-alive
# ---------------------------------------------------------------------------


def test_keepalive_touches_the_database(client):
    resp = client.get("/api/health/keepalive", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok"}
    # A cached response would never reach the database, defeating the point.
    assert resp.headers["Cache-Control"] == "no-store"


def test_keepalive_reports_an_unreachable_database(client, dead_engine, monkeypatch):
    monkeypatch.setattr(db_module, "engine", dead_engine)
    resp = client.get("/api/health/keepalive", headers=HEADERS)
    assert resp.status_code == 503
    assert resp.json() == {"status": "unreachable"}
    # Nothing about the connection string or the error leaks to the caller.
    assert "127.0.0.1" not in resp.text
    assert "aura:aura" not in resp.text


def test_vercel_crons_call_the_keepalive_daily():
    """The crons are what keep the project from pausing. Hobby plans reject any
    single cron that runs more than once a day at deploy time, so each entry
    must be a plain daily schedule."""
    config = json.loads(
        (Path(__file__).resolve().parents[2] / "vercel.json").read_text(encoding="utf-8")
    )
    crons = config.get("crons", [])
    # /api/cron/tick keeps the database awake on every call (and, with
    # CRON_SECRET set, runs the background worker too).
    keepalives = [
        c for c in crons if c["path"] in ("/api/health/keepalive", "/api/cron/tick")
    ]
    assert len(keepalives) >= 2, "want a few touches a day, not one"

    for cron in keepalives:
        minute, hour, day, month, weekday = cron["schedule"].split()
        assert minute.isdigit() and hour.isdigit(), cron
        assert (day, month, weekday) == ("*", "*", "*"), cron

    # The cron path must reach the backend service, not the Next.js frontend.
    backend_rewrites = [
        r["source"]
        for r in config["rewrites"]
        if r["destination"].get("service") == "backend"
    ]
    assert "/api/(.*)" in backend_rewrites
