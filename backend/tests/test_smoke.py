"""Smoke tests.

Run from backend/:  pytest -q
Uses a throwaway SQLite file and the mock LLM provider, so no keys or infra needed.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="module")
def auth(client):
    resp = client.post("/api/auth/demo", json={"email": "test@aura.ai", "name": "Test"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"


def test_requires_auth(client):
    assert client.get("/api/briefing").status_code == 401


def test_demo_seed_populates_workspace(client, auth):
    assert len(client.get("/api/emails", headers=auth).json()) > 0
    assert len(client.get("/api/events", headers=auth).json()) > 0
    assert len(client.get("/api/tasks", headers=auth).json()) > 0
    assert len(client.get("/api/memories", headers=auth).json()) > 0


def test_briefing_shape(client, auth):
    body = client.get("/api/briefing", headers=auth).json()
    assert body["greeting"].startswith("Good ")
    assert "Today:" in body["headline"]
    assert isinstance(body["suggested_priorities"], list)


def test_task_lifecycle(client, auth):
    created = client.post(
        "/api/tasks", headers=auth, json={"title": "Write tests", "priority": "high"}
    ).json()
    assert created["title"] == "Write tests"

    done = client.patch(
        f"/api/tasks/{created['id']}", headers=auth, json={"status": "done"}
    ).json()
    assert done["status"] == "done" and done["completed_at"]

    assert client.delete(f"/api/tasks/{created['id']}", headers=auth).status_code == 200


def test_memory_roundtrip(client, auth):
    client.post(
        "/api/memories",
        headers=auth,
        json={"content": "Prefers espresso over filter coffee", "kind": "preference"},
    )
    hits = client.get("/api/memories/search", headers=auth, params={"q": "coffee"}).json()
    assert any("espresso" in m["content"] for m in hits)


def test_automation_compiles_from_language(client, auth):
    rule = client.post(
        "/api/automations",
        headers=auth,
        json={
            "natural_language": "When I receive an email from northwind.vc, create a task and notify me."
        },
    ).json()
    assert rule["trigger_type"] == "email_received"
    assert {a["tool"] for a in rule["actions"]} & {"create_task", "notify"}


def test_free_slots_returns_slots(client, auth):
    slots = client.get(
        "/api/events/free-slots", headers=auth, params={"duration_minutes": 30, "days": 5}
    ).json()
    assert isinstance(slots, list)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


def test_skill_catalogue_is_seeded(client, auth):
    skills = client.get("/api/skills", headers=auth).json()
    codes = {s["code"] for s in skills}
    assert {"EM01", "CA01", "TK01", "MM01", "BR01"} <= codes
    assert all(s["enabled"] for s in skills)


def test_every_tool_belongs_to_a_skill():
    """A tool with no owning skill can never be disabled — catch that here."""
    from app.agent import skills as registry
    from app.agent import tools as tool_registry

    orphans = set(tool_registry.REGISTRY) - set(registry.TOOL_TO_SKILL)
    assert not orphans, f"tools without a skill: {orphans}"


def test_disabling_a_skill_hides_its_tools(client, auth):
    from app.agent import skills as registry
    from app.models import User

    client.patch("/api/skills/TK01", headers=auth, json={"enabled": False})

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        allowed = registry.enabled_tools(db, user.id)
        assert "create_task" not in allowed
        assert "summarize_inbox" in allowed
    finally:
        db.close()

    client.patch("/api/skills/TK01", headers=auth, json={"enabled": True})


def test_teaching_a_skill_persists_and_reaches_the_prompt(client, auth):
    from app.agent import skills as registry
    from app.models import User

    lesson = "Never schedule anything before 10am"
    updated = client.post(
        "/api/skills/CA01/teach", headers=auth, json={"note": lesson}
    ).json()
    assert lesson in updated["learned_notes"]

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        block = registry.learned_notes_block(db, user.id)
        assert lesson in block
    finally:
        db.close()


def test_agent_records_skill_runs(client, auth):
    body = client.post(
        "/api/chat", headers=auth, json={"message": "add a task to renew the domain"}
    ).json()
    assert body["conversation_id"]
    assert any(c["name"] == "create_task" for c in body["tool_calls"])
    assert any(r["code"] == "TK01" for r in body["skill_runs"])

    titles = [t["title"] for t in client.get("/api/tasks", headers=auth).json()]
    assert any("renew the domain" in t for t in titles)

    activity = client.get("/api/skills/activity", headers=auth).json()
    assert any(r["code"] == "TK01" for r in activity)


# ---------------------------------------------------------------------------
# Autonomy
# ---------------------------------------------------------------------------


def test_tiers_gate_the_right_risk_classes(client, auth):
    from app.agent import autonomy
    from app.models import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        needs = lambda tool, tier: autonomy.requires_approval(db, user.id, tool, tier)  # noqa: E731

        # Reading is always free.
        assert needs("summarize_inbox", "strict") is False

        # Internal writes: strict asks, conservative doesn't.
        assert needs("create_task", "strict") is True
        assert needs("create_task", "conservative") is False

        # Externally visible: conservative asks, relaxed doesn't.
        assert needs("create_event", "conservative") is True
        assert needs("create_event", "relaxed") is False

        # Irreversible: held everywhere except full autonomy.
        for tier in ("strict", "conservative", "relaxed"):
            assert needs("send_email", tier) is True
        assert needs("send_email", "full") is False
    finally:
        db.close()


def test_legacy_tier_values_still_resolve():
    from app.agent.autonomy import normalise_tier

    assert normalise_tier("ask") == "strict"
    assert normalise_tier("trusted") == "relaxed"
    assert normalise_tier(None) == "conservative"


def test_always_allow_creates_a_standing_grant(client, auth):
    from app.agent import autonomy
    from app.models import PendingAction, User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        action = PendingAction(
            user_id=user.id,
            tool_name="send_email",
            arguments={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
            preview="Send email to a@b.com",
            skill_code="EM03",
        )
        db.add(action)
        db.commit()
        action_id = action.id
        assert autonomy.requires_approval(db, user.id, "send_email", "conservative") is True
    finally:
        db.close()

    resp = client.post(
        f"/api/pending-actions/{action_id}", headers=auth, json={"decision": "always"}
    )
    assert resp.status_code == 200
    assert resp.json().get("grant", {}).get("scope") == "always"

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        # The grant now overrides the tier — that's the point.
        assert autonomy.requires_approval(db, user.id, "send_email", "conservative") is False
        autonomy.revoke(db, user.id, "send_email")
        assert autonomy.requires_approval(db, user.id, "send_email", "conservative") is True
    finally:
        db.close()


def test_rejecting_does_not_run_the_action(client, auth):
    from app.models import PendingAction, Task, User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        action = PendingAction(
            user_id=user.id,
            tool_name="delete_task",
            arguments={"task_id": "does-not-exist"},
            preview="Delete task",
        )
        db.add(action)
        db.commit()
        action_id = action.id
        before = db.query(Task).filter(Task.user_id == user.id).count()
    finally:
        db.close()

    client.post(
        f"/api/pending-actions/{action_id}", headers=auth, json={"decision": "reject"}
    )

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        assert db.query(Task).filter(Task.user_id == user.id).count() == before
        assert db.get(PendingAction, action_id).status == "rejected"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_assistant_exists_and_starts_as_a_stranger(client, auth):
    body = client.get("/api/assistant", headers=auth).json()
    assert body["name"]
    assert body["stage"] in ("stranger", "acquaintance", "colleague", "chief_of_staff")
    assert set(body["signals"]) == {"interactions", "memories", "actions"}


def test_hatching_seeds_memory_and_sets_autonomy(client, auth):
    resp = client.post(
        "/api/assistant/hatch",
        headers=auth,
        json={
            "name": "Nova",
            "personality": "dry",
            "avatar": "amber",
            "pronoun": "she",
            "goals": ["Protect my mornings"],
            "role": "Founder at a seed-stage fintech",
            "about": "I write in British English.",
            "autonomy_level": "relaxed",
        },
    ).json()
    assert resp["name"] == "Nova" and resp["onboarded"] is True

    assert client.get("/api/settings", headers=auth).json()["autonomy_level"] == "relaxed"

    memories = client.get("/api/memories", headers=auth).json()
    contents = " ".join(m["content"] for m in memories)
    assert "fintech" in contents and "British English" in contents


def test_stage_thresholds_are_monotonic():
    from app.services.identity import STAGES, compute_stage

    assert compute_stage({"interactions": 0, "memories": 0, "actions": 0}).key == "stranger"
    assert (
        compute_stage({"interactions": 999, "memories": 999, "actions": 999}).key
        == "chief_of_staff"
    )
    # Every signal must clear the bar, not just one.
    assert (
        compute_stage({"interactions": 999, "memories": 0, "actions": 999}).key == "stranger"
    )
    for earlier, later in zip(STAGES, STAGES[1:]):
        assert later.interactions >= earlier.interactions
        assert later.memories >= earlier.memories


# ---------------------------------------------------------------------------
# Heartbeat + memory maintenance
# ---------------------------------------------------------------------------


def test_heartbeat_runs_and_reports(client, auth):
    report = client.post("/api/heartbeat/run", headers=auth).json()
    assert "headline" in report
    assert isinstance(report["lines"], list)
    for line in report["lines"]:
        assert line.startswith("[SKILL·")

    latest = client.get("/api/heartbeat/latest", headers=auth).json()
    assert latest["id"] == report["id"]


def test_heartbeat_respects_quiet_hours():
    from datetime import datetime, timezone

    from app.services.heartbeat import in_quiet_hours

    night = datetime(2026, 7, 29, 23, 30, tzinfo=timezone.utc)
    midday = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    early = datetime(2026, 7, 29, 3, 0, tzinfo=timezone.utc)

    assert in_quiet_hours("22:00-07:00", night) is True
    assert in_quiet_hours("22:00-07:00", early) is True
    assert in_quiet_hours("22:00-07:00", midday) is False


def test_heartbeat_never_runs_skills_above_the_tier():
    from app.agent.autonomy import heartbeat_allows

    # Inbox Cleanup floors at "full" — nothing below it may fire unattended.
    assert heartbeat_allows("relaxed", "full") is False
    assert heartbeat_allows("full", "full") is True
    assert heartbeat_allows("strict", "strict") is True


def test_memory_compaction_merges_duplicates():
    """Insert near-duplicates directly, bypassing remember()'s own de-duping."""
    from app.models import Memory, User
    from app.services.memory import compact

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        for text in (
            "Always books flights with an aisle seat",
            "Always books flights with an aisle seat",
            "Always books flights with an aisle seat please",
        ):
            db.add(
                Memory(user_id=user.id, content=text, kind="preference", source="test")
            )
        db.commit()

        result = compact(db, user.id)
        assert result["merged"] >= 2
        assert result["after"] < result["before"]

        survivors = [
            m
            for m in db.query(Memory).filter(Memory.user_id == user.id).all()
            if "aisle seat" in m.content
        ]
        assert len(survivors) == 1
    finally:
        db.close()


def test_compaction_endpoint_is_reachable(client, auth):
    result = client.post("/api/memories/compact", headers=auth).json()
    assert set(result) == {"before", "after", "merged", "promoted", "dropped"}
    assert result["after"] <= result["before"]


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


def test_core_plugins_installed_and_undeletable(client, auth):
    plugins = client.get("/api/plugins", headers=auth).json()
    core = [p for p in plugins if p["core"]]
    assert core and all(p["installed"] for p in core)

    resp = client.post(f"/api/plugins/{core[0]['id']}/uninstall", headers=auth)
    assert resp.status_code == 400


def test_installing_a_plugin_unlocks_its_skills(client, auth):
    before = {s["code"] for s in client.get("/api/skills", headers=auth).json()}
    assert "RS01" not in before  # Research isn't installed by default

    assert client.post("/api/plugins/research/install", headers=auth).status_code == 200

    after = {s["code"] for s in client.get("/api/skills", headers=auth).json()}
    assert {"RS01", "RS02"} <= after


def test_uninstalling_hides_skills_but_keeps_history(client, auth):
    client.post("/api/plugins/documents/install", headers=auth)
    client.post("/api/skills/DC01/teach", headers=auth, json={"note": "Quote clauses verbatim"})

    client.post("/api/plugins/documents/uninstall", headers=auth)
    codes = {s["code"] for s in client.get("/api/skills", headers=auth).json()}
    assert "DC01" not in codes

    # Reinstalling brings the lesson back — the row was disabled, not destroyed.
    client.post("/api/plugins/documents/install", headers=auth)
    skills = {s["code"]: s for s in client.get("/api/skills", headers=auth).json()}
    assert "Quote clauses verbatim" in skills["DC01"]["learned_notes"]


def test_unavailable_plugins_cannot_be_installed(client, auth):
    resp = client.post("/api/plugins/travel/install", headers=auth)
    assert resp.status_code == 400
    assert "not built yet" in resp.json()["detail"]


def test_locked_skill_tools_never_reach_the_model(client, auth):
    """A tool from an uninstalled plugin must be invisible, not merely discouraged."""
    from app.agent import skills as registry
    from app.models import User

    client.post("/api/plugins/delegation/uninstall", headers=auth)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        assert "delegate_task" not in registry.enabled_tools(db, user.id)
    finally:
        db.close()

    client.post("/api/plugins/delegation/install", headers=auth)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        assert "delegate_task" in registry.enabled_tools(db, user.id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


def test_channel_connect_issues_token_once(client, auth):
    connected = client.post(
        "/api/channels/connect", headers=auth, json={"kind": "cli"}
    ).json()
    assert connected["token"]

    # Listing never leaks it again.
    listed = {c["kind"]: c for c in client.get("/api/channels", headers=auth).json()}
    assert listed["cli"]["connected"] is True
    assert not listed["cli"].get("token")


def test_inbound_requires_a_valid_token(client, auth):
    token = client.post(
        "/api/channels/connect", headers=auth, json={"kind": "cli"}
    ).json()["token"]

    assert (
        client.post(
            "/api/channels/inbound/cli", json={"token": "nonsense", "text": "hi"}
        ).status_code
        == 401
    )

    resp = client.post(
        "/api/channels/inbound/cli",
        json={"token": token, "text": "add a task to file the VAT return"},
    )
    assert resp.status_code == 200
    assert resp.json()["reply"]

    titles = [t["title"] for t in client.get("/api/tasks", headers=auth).json()]
    assert any("VAT return" in t for t in titles)


def test_rotating_invalidates_the_old_token(client, auth):
    old = client.post(
        "/api/channels/connect", headers=auth, json={"kind": "telegram"}
    ).json()["token"]
    new = client.post("/api/channels/telegram/rotate", headers=auth).json()["token"]
    assert new and new != old

    assert (
        client.post(
            "/api/channels/inbound/telegram", json={"token": old, "text": "hello"}
        ).status_code
        == 401
    )


def test_web_channel_cannot_be_disconnected(client, auth):
    assert client.post("/api/channels/web/disconnect", headers=auth).status_code == 400


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------


def test_vault_never_returns_a_value(client, auth):
    client.put(
        "/api/vault",
        headers=auth,
        json={"key": "stripe_key", "value": "sk_live_abcdef123456", "label": "Stripe"},
    )
    body = client.get("/api/vault", headers=auth).json()
    row = next(s for s in body if s["key"] == "stripe_key")
    assert "value" not in row
    assert "sk_live_abcdef123456" not in str(body)
    assert row["hint"] == "sk_…456"


def test_vault_substitution_happens_outside_the_model(client, auth):
    from app.models import User
    from app.services import vault as vault_service

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        vault_service.put(db, user.id, "api_token", "supersecret-value-1234")

        # What the model is told: names only.
        block = vault_service.prompt_block(db, user.id)
        assert "api_token" in block
        assert "supersecret-value-1234" not in block

        # What goes on the wire: the real thing.
        resolved = vault_service.resolve(
            db, user.id, "Authorization: Bearer {{vault:api_token}}"
        )
        assert resolved == "Authorization: Bearer supersecret-value-1234"

        # And anything that leaks back gets scrubbed.
        assert "{{vault:api_token}}" in vault_service.redact(
            db, user.id, "oops supersecret-value-1234 leaked"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def test_document_ingest_chunks_and_searches(client, auth):
    created = client.post(
        "/api/documents/text",
        headers=auth,
        json={
            "title": "Supplier agreement",
            "content": (
                "# Supplier agreement\n\n"
                "The supplier shall deliver within thirty days of order.\n\n"
                "Either party may terminate with sixty days written notice.\n\n"
                "Payment terms are net forty-five from invoice date.\n\n"
                "Liability is capped at the value of the preceding twelve months of fees."
            ),
        },
    ).json()
    assert created["id"]

    detail = client.get(f"/api/documents/{created['id']}", headers=auth).json()
    assert detail["chunk_count"] >= 1

    hits = client.get(
        "/api/documents/search", headers=auth, params={"q": "termination notice period"}
    ).json()
    assert hits
    assert any("terminate" in h["excerpt"].lower() for h in hits)


def test_chunking_respects_paragraphs():
    from app.services.documents import chunk_text

    text = "\n\n".join(f"Paragraph {i} " + ("word " * 60) for i in range(8))
    chunks = chunk_text(text)
    assert len(chunks) > 1
    # No chunk should start mid-word.
    assert all(chunk == chunk.strip() for chunk in chunks)


def test_unsupported_upload_is_rejected(client, auth):
    resp = client.post(
        "/api/documents/upload",
        headers=auth,
        files={"file": ("image.bin", b"\x00\x01\x02\xff\xfe", "application/octet-stream")},
    )
    assert resp.status_code in (415, 422)


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


def test_natural_language_becomes_cron():
    from app.services.schedules import describe, parse_natural

    assert parse_natural("every weekday at 7:30am")[0] == "30 7 * * 1-5"
    assert parse_natural("every day at 6pm")[0] == "0 18 * * *"
    assert parse_natural("every 15 minutes")[0] == "*/15 * * * *"
    assert "weekdays" in describe("30 7 * * 1-5")


def test_cron_matcher():
    from datetime import datetime, timezone

    from app.services.schedules import cron_matches

    at_0730_tuesday = datetime(2026, 7, 28, 7, 30, tzinfo=timezone.utc)
    assert cron_matches("30 7 * * 1-5", at_0730_tuesday) is True
    assert cron_matches("30 8 * * 1-5", at_0730_tuesday) is False
    assert cron_matches("*/15 * * * *", at_0730_tuesday) is True
    assert cron_matches("nonsense", at_0730_tuesday) is False


def test_schedule_runs_through_the_agent(client, auth):
    created = client.post(
        "/api/schedules",
        headers=auth,
        json={
            "prompt": "summarise my inbox",
            "natural_language": "every weekday at 8am",
        },
    ).json()
    assert created["cron"] == "0 8 * * 1-5"
    assert "weekdays" in created["cron_label"]

    outcome = client.post(f"/api/schedules/{created['id']}/run", headers=auth).json()
    assert outcome["text"]

    after = client.get("/api/schedules", headers=auth).json()
    row = next(s for s in after if s["id"] == created["id"])
    assert row["run_count"] == 1 and row["last_run_at"]


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Google connection
# ---------------------------------------------------------------------------


def test_integration_status_shape(client, auth):
    rows = {i["provider"]: i for i in client.get("/api/auth/integrations", headers=auth).json()}
    google = rows["google"]
    assert google["connected"] is False
    assert {c["key"] for c in google["capabilities"]} == {
        "read_email",
        "send_email",
        "calendar",
    }
    # Nothing is granted while disconnected — the card must not imply otherwise.
    assert all(c["granted"] is False for c in google["capabilities"])


def test_google_start_requires_configuration(client, auth):
    """Without a client ID the button should say so, not 500."""
    resp = client.get("/api/auth/google/start", headers=auth)
    assert resp.status_code == 503
    assert "GOOGLE_CLIENT_ID" in resp.json()["detail"]


def test_start_binds_the_signed_in_user(client, auth, monkeypatch):
    """The whole point: connecting must attach to *this* account.

    Before this was bound to the CSRF state, the callback's only clue about who
    the tokens belonged to was the Google email — so connecting a personal Gmail
    while signed in as someone else silently switched you to a different account.
    """
    from app.api import auth as auth_api
    from app.models import User

    monkeypatch.setattr(settings, "google_client_id", "test-client")
    monkeypatch.setattr(settings, "google_client_secret", "test-secret")

    body = client.get("/api/auth/google/start", headers=auth).json()
    assert body["mode"] == "connect"

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@aura.ai").one()
        entry = auth_api._oauth_state[body["state"]]
        assert entry["user_id"] == user.id
        assert entry["mode"] == "connect"
    finally:
        db.close()


def test_start_without_a_session_is_signin_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "test-client")
    monkeypatch.setattr(settings, "google_client_secret", "test-secret")

    body = client.get("/api/auth/google/start").json()
    assert body["mode"] == "signin"

    from app.api import auth as auth_api

    assert auth_api._oauth_state[body["state"]]["user_id"] is None


def test_callback_rejects_unknown_state(client):
    resp = client.get(
        "/api/auth/google/callback",
        params={"code": "x", "state": "never-issued"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "reason=bad_state" in resp.headers["location"]


def test_callback_reports_a_cancelled_consent(client):
    resp = client.get(
        "/api/auth/google/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert "reason=denied" in resp.headers["location"]


def test_capabilities_reflect_actual_granted_scopes():
    """Google lets users untick scopes, so 'connected' != 'can do the thing'."""
    from app.models import OAuthAccount
    from app.services import google as google_service

    full = OAuthAccount(
        scopes=(
            "https://www.googleapis.com/auth/gmail.modify "
            "https://www.googleapis.com/auth/calendar"
        ),
        refresh_token_enc="x",
    )
    assert all(c["granted"] for c in google_service.granted_capabilities(full))
    assert google_service.needs_reconnect(full) is False

    partial = OAuthAccount(
        scopes="https://www.googleapis.com/auth/gmail.readonly", refresh_token_enc="x"
    )
    caps = {c["key"]: c["granted"] for c in google_service.granted_capabilities(partial)}
    assert caps["read_email"] is True
    assert caps["send_email"] is False
    assert caps["calendar"] is False
    assert google_service.needs_reconnect(partial) is True


def test_missing_refresh_token_forces_a_reconnect():
    """An access token alone expires in an hour and can't be renewed."""
    from app.models import OAuthAccount
    from app.services import google as google_service

    account = OAuthAccount(
        scopes=(
            "https://www.googleapis.com/auth/gmail.modify "
            "https://www.googleapis.com/auth/calendar"
        ),
        access_token_enc="x",
        refresh_token_enc="",
    )
    assert google_service.needs_reconnect(account) is True


def test_sync_endpoint_refuses_when_disconnected(client, auth):
    resp = client.post("/api/auth/integrations/google/sync", headers=auth)
    assert resp.status_code == 400
    assert "isn't connected" in resp.json()["detail"]


def test_disconnect_is_idempotent(client, auth):
    body = client.delete("/api/auth/integrations/google", headers=auth).json()
    assert body["revoked"] is False


def test_delegation_lifecycle(client, auth):
    client.post("/api/plugins/delegation/install", headers=auth)
    created = client.post(
        "/api/delegations",
        headers=auth,
        json={"assignee": "sam@aura-design.co", "title": "Ship dashboard v4"},
    ).json()
    assert created["assignee_email"] == "sam@aura-design.co"

    updated = client.patch(
        f"/api/delegations/{created['id']}", headers=auth, params={"status": "done"}
    ).json()
    assert updated["status"] == "done"

    assert client.delete(f"/api/delegations/{created['id']}", headers=auth).status_code == 200
