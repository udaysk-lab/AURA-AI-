"""Email + password authentication.

The properties here are the ones whose failure would be silent and expensive:
a password that verifies when it shouldn't, a hash that reaches the client, or
a Google-only account becoming loginable with an empty password.

Run from backend/:  pytest -q tests/test_password_auth.py
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_auth.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password, verify_password  # noqa: E402


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


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
