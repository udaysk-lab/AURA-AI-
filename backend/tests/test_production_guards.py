"""The guards that only matter when a real person is using this.

Everything here is a thing that would be invisible until it cost money, leaked a
credential, or let someone in who shouldn't be.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_guards.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from tests.schema import drop_test_schema, ensure_test_schema  # noqa: E402


@pytest.fixture(scope="module")
def client():
    drop_test_schema(engine)
    ensure_test_schema(engine)
    with TestClient(app) as c:
        yield c
    drop_test_schema(engine)


@pytest.fixture(scope="module")
def auth(client):
    resp = client.post("/api/auth/demo", json={"email": "guard@aura.ai", "name": "Guard"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _user(db):
    from app.models import User

    return db.query(User).filter(User.email == "guard@aura.ai").one()


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def test_preflight_endpoint_is_reachable_without_auth(client):
    body = client.get("/api/health/preflight").json()
    assert "checks" in body and body["checks"]
    assert {"key", "label", "level", "detail"} <= set(body["checks"][0])


def test_preflight_never_leaks_a_value(client, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-supersecret-do-not-leak")
    body = client.get("/api/health/preflight").text
    assert "supersecret" not in body


def test_dev_secret_is_flagged_outside_local(monkeypatch):
    from app import preflight

    monkeypatch.setattr(settings, "secret_key", preflight.DEV_SECRET)
    monkeypatch.setattr(settings, "environment", "production")
    check = preflight._check_secret_key()
    assert check.level == "fail"
    assert "SECRET_KEY" in check.fix


def test_demo_login_is_fatal_in_production(monkeypatch):
    from app import preflight

    monkeypatch.setattr(settings, "allow_demo_login", True)
    monkeypatch.setattr(settings, "environment", "production")
    assert preflight._check_demo_login().level == "fail"

    monkeypatch.setattr(settings, "allow_demo_login", False)
    assert preflight._check_demo_login().level == "ok"


def test_wildcard_cors_is_fatal(monkeypatch):
    from app import preflight

    monkeypatch.setattr(settings, "cors_origins", "*")
    assert preflight._check_cors().level == "fail"


def test_sqlite_is_fatal_in_production(monkeypatch):
    from app import preflight

    monkeypatch.setattr(settings, "database_url", "sqlite:///./x.db")
    monkeypatch.setattr(settings, "environment", "production")
    assert preflight._check_database().level == "fail"


def test_readiness_probe_reports_database(client):
    body = client.get("/health/ready").json()
    assert body["checks"]["database"] == "ok"


# ---------------------------------------------------------------------------
# Schema guard: a failed upgrade is a 503 on /api, never a crashed start-up
# ---------------------------------------------------------------------------


def _failed_schema(monkeypatch, *, transient: bool, attempted_s_ago: float = 0.0):
    from app import migrate

    report = migrate.MigrationReport(
        state_before=migrate.State.PARTIAL, revision_before=None, revision_after=None,
        actions=[], backup_path=None, cached=False, duration_ms=1, ok=False,
        transient=transient, error="users.email is missing",
    )
    monkeypatch.setattr(migrate, "_LAST", report)
    monkeypatch.setattr(migrate, "_LAST_ATTEMPT", migrate._monotonic() - attempted_s_ago)
    return report


def _counting_ensure(monkeypatch, outcome):
    """Replace ensure_schema; `outcome` is the report to leave behind."""
    from app import migrate

    calls: list[str] = []

    def fake(context):
        calls.append(context)
        monkeypatch.setattr(migrate, "_LAST", outcome)
        return outcome

    monkeypatch.setattr(migrate, "ensure_schema", fake)
    return calls


def test_schema_guard_returns_503_on_api(client, auth, monkeypatch):
    _failed_schema(monkeypatch, transient=False)
    resp = client.get("/api/briefing", headers=auth)
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "30"
    assert resp.json() == {
        "detail": "AURA is finishing a database update. Try again shortly.",
        "code": "schema_not_ready",
    }


def test_schema_guard_allows_health_and_preflight(client, monkeypatch):
    _failed_schema(monkeypatch, transient=False)
    health = client.get("/health")
    assert health.status_code == 200 and health.json()["schema"] == "not_ready"
    assert client.get("/health/ready").status_code == 200
    resp = client.get("/api/health/preflight")
    assert resp.status_code == 200
    schema = next(c for c in resp.json()["checks"] if c["key"] == "schema")
    assert schema["level"] == "warn" and "users.email" in schema["detail"]


def test_guard_retries_transient_after_30s(client, auth, monkeypatch):
    from app import migrate

    recovered = migrate.MigrationReport(
        state_before=migrate.State.AT_HEAD, revision_before=migrate.EXPECTED_HEAD,
        revision_after=migrate.EXPECTED_HEAD, actions=[], backup_path=None, cached=False,
        duration_ms=1, ok=True, transient=False, error=None,
    )
    _failed_schema(monkeypatch, transient=True, attempted_s_ago=5)
    calls = _counting_ensure(monkeypatch, recovered)

    assert client.get("/api/briefing", headers=auth).status_code == 503
    assert calls == []  # tried 5 s ago: not again yet

    monkeypatch.setattr(migrate, "_LAST_ATTEMPT", migrate._monotonic() - 31)
    _failed_schema_report = migrate.MigrationReport(
        state_before=None, revision_before=None, revision_after=None, actions=[],
        backup_path=None, cached=False, duration_ms=1, ok=False, transient=True, error="lock",
    )
    monkeypatch.setattr(migrate, "_LAST", _failed_schema_report)
    resp = client.get("/api/briefing", headers=auth)
    assert calls == ["api"] and resp.status_code != 503  # recovered, request served


def test_guard_does_not_retry_deterministic(client, auth, monkeypatch):
    from app import migrate

    _failed_schema(monkeypatch, transient=False, attempted_s_ago=3600)
    calls = _counting_ensure(monkeypatch, migrate._LAST)
    assert client.get("/api/briefing", headers=auth).status_code == 503
    assert calls == []


def test_preflight_schema_warn_not_fail_in_production(monkeypatch):
    """A production preflight failure raises in the lifespan and would hide the cause."""
    from app import migrate, preflight

    monkeypatch.setattr(settings, "environment", "production")
    _failed_schema(monkeypatch, transient=False)
    check = preflight._check_schema()
    assert check.level == "warn" and "Redeploy" in check.fix
    assert "schema" not in {c.key for c in preflight.run().failures}

    ahead = migrate.MigrationReport(
        state_before=migrate.State.AHEAD, revision_before="zz99", revision_after="zz99",
        actions=[], backup_path=None, cached=False, duration_ms=1, ok=True, transient=False, error=None,
    )
    monkeypatch.setattr(migrate, "_LAST", ahead)
    assert preflight._check_schema().level == "warn"

    monkeypatch.setattr(migrate, "_LAST", ahead.__class__(**{**ahead.__dict__, "state_before": migrate.State.AT_HEAD, "revision_after": migrate.EXPECTED_HEAD}))
    ok = preflight._check_schema()
    assert ok.level == "ok" and ok.detail == migrate.EXPECTED_HEAD


def test_health_reports_schema(client):
    from app import migrate

    assert client.get("/health").json()["schema"] == migrate.EXPECTED_HEAD


# ---------------------------------------------------------------------------
# Spend guard
# ---------------------------------------------------------------------------


def test_cost_estimate_uses_the_price_table():
    from app.llm import Usage
    from app.services.usage import estimate_cost

    # 1M in + 1M out on Sonnet 5.5 at (2, 10) per million.
    cost = estimate_cost(
        Usage(input_tokens=1_000_000, output_tokens=1_000_000, model="claude-sonnet-5-5")
    )
    assert cost == pytest.approx(12.0)


def test_cost_estimate_follows_a_tiered_price():
    """Haiku 5.5 costs five times as much once one prompt passes 100k tokens."""
    from app.llm import Usage
    from app.services.usage import estimate_cost

    short = estimate_cost(Usage(input_tokens=50_000, output_tokens=0, model="claude-haiku-5-5"))
    long = estimate_cost(Usage(input_tokens=200_000, output_tokens=0, model="claude-haiku-5-5"))
    assert short == pytest.approx(50_000 / 1_000_000 * 0.10)
    assert long == pytest.approx(200_000 / 1_000_000 * 0.50)


def test_unknown_model_is_priced_pessimistically():
    from app.services.usage import DEFAULT_PRICE, price_for

    assert price_for("some-model-nobody-has-heard-of") == DEFAULT_PRICE
    # Versioned model names still resolve by prefix.
    assert price_for("claude-sonnet-5-20260501") == (2.00, 10.00)
    # The longest known prefix wins: Opus 5.5 is not priced as the older Opus 5.
    assert price_for("claude-opus-5-5") == (4.00, 20.00)
    assert price_for("claude-opus-5-5-20261001") == (4.00, 20.00)
    assert price_for("claude-opus-5") == (15.00, 75.00)


def test_zero_token_usage_records_nothing(client, auth):
    from app.llm import Usage
    from app.services import usage as usage_service

    db = SessionLocal()
    try:
        user = _user(db)
        assert usage_service.record(db, user.id, Usage()) is None
    finally:
        db.close()


def test_spend_cap_blocks_once_exceeded(client, auth, monkeypatch):
    from app.llm import Usage
    from app.services import usage as usage_service

    monkeypatch.setattr(settings, "spend_cap_enabled", True)
    monkeypatch.setattr(settings, "daily_spend_cap_usd", 0.10)

    db = SessionLocal()
    try:
        user = _user(db)
        # Under the cap: fine.
        usage_service.check(db, user.id)

        # Burn through it.
        usage_service.record(
            db, user.id,
            Usage(input_tokens=100_000, output_tokens=100_000, model="claude-sonnet-5"),
        )
        with pytest.raises(usage_service.SpendCapReached):
            usage_service.check(db, user.id)
    finally:
        db.close()


def test_background_work_gets_a_smaller_budget(client, auth, monkeypatch):
    """Unattended spending must never starve the interactive budget."""
    from app.llm import Usage
    from app.services import usage as usage_service

    monkeypatch.setattr(settings, "spend_cap_enabled", True)
    monkeypatch.setattr(settings, "daily_spend_cap_usd", 1.00)

    from app.models import UsageRecord

    db = SessionLocal()
    try:
        user = _user(db)
        # Start from a clean slate — an earlier test deliberately blew the cap.
        db.query(UsageRecord).filter(UsageRecord.user_id == user.id).delete()
        db.commit()

        # $0.50 spent: half the cap, but above the 40% background allowance.
        usage_service.record(
            db, user.id,
            Usage(input_tokens=0, output_tokens=50_000, model="claude-sonnet-5"),
        )
        state = usage_service.spend_today(db, user.id)
        assert 0.4 < state.spent_usd < 0.6

        usage_service.check(db, user.id, trigger="chat")  # interactive: allowed
        with pytest.raises(usage_service.SpendCapReached):
            usage_service.check(db, user.id, trigger="heartbeat")
    finally:
        db.close()


def test_spend_endpoint_shape(client, auth):
    body = client.get("/api/account/spend", headers=auth).json()
    assert {"spent_usd", "cap_usd", "remaining_usd", "percent", "provider"} <= set(body)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_memory_bucket_enforces_the_window():
    from app.middleware import MemoryBuckets

    buckets = MemoryBuckets()
    for _ in range(3):
        allowed, _ = buckets.hit("k", limit=3)
        assert allowed is True

    allowed, retry_after = buckets.hit("k", limit=3)
    assert allowed is False
    assert retry_after >= 1


def test_buckets_are_keyed_independently():
    from app.middleware import MemoryBuckets

    buckets = MemoryBuckets()
    assert buckets.hit("a", limit=1)[0] is True
    assert buckets.hit("a", limit=1)[0] is False
    assert buckets.hit("b", limit=1)[0] is True


def test_inbound_is_limited_harder_than_authenticated_api():
    assert settings.rate_limit_inbound_per_minute < settings.rate_limit_per_minute


def test_health_is_never_rate_limited(client):
    # Must stay reachable so a limited client can still find out why.
    for _ in range(30):
        assert client.get("/health").status_code == 200


def test_responses_carry_a_request_id(client):
    resp = client.get("/health")
    assert resp.headers.get("X-Request-ID")


def test_security_headers_are_present(client):
    headers = client.get("/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"


# ---------------------------------------------------------------------------
# Sending email
# ---------------------------------------------------------------------------


def test_send_reports_failure_honestly_with_no_route(client, auth):
    """The one unacceptable outcome is claiming success for an unsent email."""
    from app.services import mailer

    db = SessionLocal()
    try:
        user = _user(db)
        result = mailer.send(db, user, "someone@example.com", "Hi", "Body")
        assert result["sent"] is False
        assert result["reason"] == "no_route"
        assert result["draft_preserved"] is True
    finally:
        db.close()


def test_send_rejects_a_malformed_address(client, auth):
    from app.services import mailer

    db = SessionLocal()
    try:
        user = _user(db)
        result = mailer.send(db, user, "not-an-address", "Hi", "Body")
        assert result["sent"] is False and result["reason"] == "invalid_recipient"
    finally:
        db.close()


def test_send_email_tool_never_claims_a_send_it_didnt_make(client, auth):
    from app.agent.coordinator import execute_tool

    db = SessionLocal()
    try:
        user = _user(db)
        result = execute_tool(
            db, user, "send_email",
            {"to": "a@b.com", "subject": "Hello", "body": "Test"},
        )
        assert result["sent"] is False
        assert "nothing was sent" in result["message"].lower()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Data rights
# ---------------------------------------------------------------------------


def test_export_excludes_secret_values(client, auth):
    client.put(
        "/api/vault",
        headers=auth,
        json={"key": "leak_check", "value": "PLAINTEXT-SHOULD-NEVER-APPEAR"},
    )
    body = client.get("/api/account/export", headers=auth).text
    assert "PLAINTEXT-SHOULD-NEVER-APPEAR" not in body
    assert "leak_check" in body          # the name is fine
    assert "value_enc" not in body       # the ciphertext column is excluded too


def test_export_includes_the_users_own_data(client, auth):
    client.post(
        "/api/memories",
        headers=auth,
        json={"content": "Exports should contain this", "kind": "fact"},
    )
    body = client.get("/api/account/export", headers=auth).json()
    assert any("Exports should contain this" in m["content"] for m in body["memories"])
    assert body["_note"]


def test_delete_requires_the_exact_email(client, auth):
    assert (
        client.post(
            "/api/account/delete",
            headers=auth,
            json={"confirm_email": "wrong@example.com", "understand": True},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/account/delete",
            headers=auth,
            json={"confirm_email": "guard@aura.ai", "understand": False},
        ).status_code
        == 400
    )


def test_delete_cascades_everything(client, auth):
    from app.models import Memory, User

    resp = client.post(
        "/api/account/delete",
        headers=auth,
        json={"confirm_email": "guard@aura.ai", "understand": True},
    )
    assert resp.status_code == 200 and resp.json()["deleted"] is True

    db = SessionLocal()
    try:
        assert db.query(User).filter(User.email == "guard@aura.ai").count() == 0
        # Orphaned child rows would mean the cascade is misconfigured.
        assert db.query(Memory).count() == 0
    finally:
        db.close()

    # The token is now worthless.
    assert client.get("/api/auth/me", headers=auth).status_code == 401
