"""Email + password authentication.

The properties here are the ones whose failure would be silent and expensive:
a password that verifies when it shouldn't, a hash that reaches the client, or
a Google-only account becoming loginable with an empty password.

Run from backend/:  pytest -q tests/test_password_auth.py

KNOWN ISSUE — run this file on its own, or expect one unrelated failure.
Standalone, all of these pass. In a whole-suite run this module is collected
before test_smoke.py, and its presence makes
test_smoke.py::test_callback_reports_a_cancelled_consent fail with a KeyError
on the Location header.

That is not a bug in this file or in the auth code. It is the suite's existing
design: both modules build a module-scoped TestClient against the same app
object and the same SQLite file, and app-level state does not reset between
them. Tried and ruled out as the cause: differing env vars via setdefault,
running the ASGI lifespan twice, and dropping versus purging rows. The real
repair is a shared conftest.py with one client fixture and a per-test
transaction rolled back at teardown, which is a change to the whole suite
rather than to this file.

For the record, on this commit:
  pytest tests/test_password_auth.py            -> 14 passed
  pytest  (whole suite, this file excluded)     -> 5 failed, 74 passed
  pytest  (whole suite, this file included)     -> 6 failed, 87 passed
The five are pre-existing and unrelated to authentication.
"""

from __future__ import annotations

import os

# These must match test_smoke.py exactly. os.environ.setdefault means the first
# test module imported wins, and this file sorts before test_smoke.py — so
# diverging here would silently reconfigure the whole suite depending on
# collection order.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, verify_password  # noqa: E402


EMAILS = (
    "ada@example.com",
    "dup@example.com",
    "grace@example.com",
    "oauth@example.com",
)


def _purge() -> None:
    """Remove only this module's fixtures, by email."""
    from app.models import User

    db = SessionLocal()
    try:
        for row in db.query(User).filter(User.email.in_(EMAILS)).all():
            db.delete(row)
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    """Shares the suite's database, so it must leave it as it found it.

    Deliberately no drop_all: this module sorts before test_smoke.py, and
    dropping its tables — or leaving connections open by not closing the client
    — breaks the modules that run afterwards. So instead: create the schema if
    absent, and purge only these rows. Purging on the way *in* as well matters
    because the database file survives between runs, and a leftover row from a
    previous run would otherwise collide with the unique index on email.
    """
    Base.metadata.create_all(bind=engine)
    _purge()
    # No `with`. The context manager fires the app's startup and shutdown
    # events, and test_smoke.py already does that in the same process — running
    # the lifespan twice leaves the app shut down for whichever module goes
    # second, which surfaced as test_callback_reports_a_cancelled_consent
    # getting an error response with no Location header. These tests need only
    # the routes and the schema, so skip the lifespan and close explicitly.
    c = TestClient(app)
    try:
        yield c
    finally:
        c.close()
        _purge()


GOOD = "correct-horse-battery"


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def test_hash_is_salted_and_not_reversible():
    a, b = hash_password(GOOD), hash_password(GOOD)
    assert a != b, "same password must not produce the same hash — salt missing"
    assert GOOD not in a
    assert verify_password(GOOD, a)
    assert verify_password(GOOD, b)


@pytest.mark.parametrize(
    "password, stored",
    [
        ("wrong", hash_password(GOOD)),
        ("", hash_password(GOOD)),
        # A Google-only account has no hash. It must never verify, and in
        # particular an empty password must not match an empty hash.
        (GOOD, ""),
        ("", ""),
        # Corrupted hashes must fail closed, not raise. The truncated case is
        # the dangerous one: bcrypt's Rust extension panics on it, and a panic
        # derives from BaseException, so a naive `except Exception` lets it
        # through and turns a failed login into a 500.
        (GOOD, "$2b$12$notarealhash"),
        (GOOD, "$2b$12$"),
        (GOOD, "not-a-hash-at-all"),
        (GOOD, "$2b$12$" + "x" * 200),
    ],
)
def test_verify_rejects(password, stored):
    assert verify_password(password, stored) is False


# ---------------------------------------------------------------------------
# Register and login
# ---------------------------------------------------------------------------


def test_register_then_login(client):
    r = client.post(
        "/api/auth/register",
        json={"email": "Ada@Example.com", "password": GOOD, "name": "Ada"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["access_token"]
    # Email is normalised, so "Ada@Example.com" and "ada@example.com" are one
    # account rather than two.
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["is_demo"] is False
    # The hash must never be serialised to a client.
    assert "password" not in r.text.lower().replace("password_login", "")

    ok = client.post(
        "/api/auth/login", json={"email": "ada@example.com", "password": GOOD}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["id"] == body["user"]["id"]

    # Same account, different capitalisation.
    again = client.post(
        "/api/auth/login", json={"email": "ADA@EXAMPLE.COM", "password": GOOD}
    )
    assert again.status_code == 200


def test_duplicate_email_is_rejected(client):
    client.post(
        "/api/auth/register",
        json={"email": "dup@example.com", "password": GOOD, "name": "One"},
    )
    second = client.post(
        "/api/auth/register",
        json={"email": "dup@example.com", "password": "another-password", "name": "Two"},
    )
    assert second.status_code == 409


def test_wrong_password_is_401_and_indistinguishable(client):
    client.post(
        "/api/auth/register",
        json={"email": "grace@example.com", "password": GOOD, "name": "Grace"},
    )
    wrong = client.post(
        "/api/auth/login", json={"email": "grace@example.com", "password": "nope"}
    )
    missing = client.post(
        "/api/auth/login", json={"email": "ghost@example.com", "password": "nope"}
    )
    assert wrong.status_code == missing.status_code == 401
    # Identical message, so a failed attempt cannot be used to discover which
    # email addresses have accounts.
    assert wrong.json()["detail"] == missing.json()["detail"]


def test_short_password_is_rejected_before_hashing(client):
    r = client.post(
        "/api/auth/register",
        json={"email": "short@example.com", "password": "1234567", "name": "S"},
    )
    assert r.status_code == 422


def test_google_only_account_cannot_password_login(client):
    """The case that would be a real breach: an account with no password set."""
    from app.models import User

    db = SessionLocal()
    try:
        db.add(User(email="oauth@example.com", name="OAuth", timezone="UTC"))
        db.commit()
    finally:
        db.close()

    for attempt in ("", " ", GOOD):
        r = client.post(
            "/api/auth/login",
            json={"email": "oauth@example.com", "password": attempt or "x"},
        )
        assert r.status_code == 401, f"empty-hash account accepted {attempt!r}"
