"""The permissions API (spec 10 section 11, contract notes 11): mode, boundaries, catalogue, rules
(including PATCH), the event log and its verification; presence on every change; removed routes."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, update  # noqa: E402

from app import deps  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.device.runtime import reset_cache  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    ApprovalGrant, Notification, PairedDevice, PermissionEvent, PermissionRule, User, UserSettings, utcnow,
)
from app.permissions import access, catalogue, reviewer  # noqa: E402
from app.security import create_access_token  # noqa: E402
from tests.perm_helpers import apply_legacy  # noqa: E402
from tests.schema import drop_test_schema, ensure_test_schema  # noqa: E402

KEY = "api-test-key"
STATE_KEYS = {"mode", "mode_label", "auto_available", "auto_effective", "reviewer_model", "boundaries"}
RULE_KEYS = {"id", "effect", "system", "tool", "target_kind", "pattern", "applies_to", "source", "expires_at",
             "revoked_at", "note", "created_at"}
EVENT_KEYS = {"seq", "actor", "kind", "tool", "target", "outcome", "reason_code", "run_key", "detail", "created_at"}


@pytest.fixture(scope="module")
def client(as_peer):
    drop_test_schema(engine)
    ensure_test_schema(engine)
    with TestClient(as_peer(app), base_url="http://127.0.0.1") as c:
        yield c
    drop_test_schema(engine)


@pytest.fixture(autouse=True)
def local(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    monkeypatch.setattr(settings, "remote_access", True, raising=False)
    monkeypatch.setattr(settings, "aura_home", str(tmp_path / "aura-home"), raising=False)
    monkeypatch.setattr(settings, "device_allowed_roots", "", raising=False)
    monkeypatch.setattr(settings, "device_command_denylist", "", raising=False)
    monkeypatch.setattr(settings, "llm_provider", "mock", raising=False)
    monkeypatch.setattr(deps, "_generated_local_key", "", raising=False)
    reset_cache()
    access.reset_state()
    yield
    access.reset_state()
    reset_cache()


def sign_in(client, key=KEY, configured=KEY):
    saved = settings.aura_local_login_key
    settings.aura_local_login_key = configured
    try:
        return client.post("/api/auth/local", json={"key": key}) if key else client.post("/api/auth/local")
    finally:
        settings.aura_local_login_key = saved


def bearer(r):
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def h(client):
    r = sign_in(client)
    assert r.status_code == 200, r.text
    return bearer(r)


def me(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def reset_user(client, h):
    """Mode ask, no boundaries, no rules: a clean owner for each test."""
    uid = me(client, h)
    db = SessionLocal()
    try:
        row = db.scalars(select(UserSettings).where(UserSettings.user_id == uid)).first()
        if row:
            row.permission_mode, row.reviewer_boundaries = "ask", ""
        db.execute(update(PermissionRule).where(PermissionRule.user_id == uid).values(revoked_at=utcnow()))
        db.commit()
    finally:
        db.close()
    return uid


@pytest.fixture
def owner(client, h):
    reset_user(client, h)
    return h


@pytest.fixture
def auto_works(monkeypatch):
    monkeypatch.setattr(reviewer, "available", lambda: True)


def events_of(uid, kind=None):
    db = SessionLocal()
    try:
        q = select(PermissionEvent).where(PermissionEvent.user_id == uid).order_by(PermissionEvent.seq)
        return [e for e in db.scalars(q).all() if kind is None or e.kind == kind]
    finally:
        db.close()


def notes_of(uid):
    db = SessionLocal()
    try:
        return db.scalars(select(Notification).where(Notification.user_id == uid).order_by(Notification.created_at)).all()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Mode and boundaries
# ---------------------------------------------------------------------------


def test_state_shape_and_defaults(client, owner):
    r = client.get("/api/permissions", headers=owner)
    assert r.status_code == 200
    assert set(r.json()) == STATE_KEYS
    assert r.json() | {"reviewer_model": ""} == {
        "mode": "ask", "mode_label": "Ask first", "auto_available": False, "auto_effective": False,
        "reviewer_model": "", "boundaries": ""}
    assert client.get("/api/permissions").status_code == 401


def test_mode_round_trip_is_logged_and_announced(client, owner):
    uid = me(client, owner)
    before_events, before_notes = len(events_of(uid, "mode_changed")), len(notes_of(uid))
    r = client.put("/api/permissions/mode", headers=owner, json={"mode": "skip"})
    assert r.status_code == 200 and set(r.json()) == STATE_KEYS
    assert (r.json()["mode"], r.json()["mode_label"]) == ("skip", "Skip approvals")
    assert client.get("/api/permissions", headers=owner).json()["mode"] == "skip"
    (ev,) = events_of(uid, "mode_changed")[before_events:]
    assert (ev.actor, ev.detail) == ("user", {"from": "ask", "to": "skip"})
    note = notes_of(uid)[before_events and before_notes:][-1]
    assert note.level == "urgent" and "widened" in note.title.lower()
    # narrowing is an info note; saying the same thing again changes nothing
    client.put("/api/permissions/mode", headers=owner, json={"mode": "ask"})
    assert notes_of(uid)[-1].level == "info"
    count = len(events_of(uid, "mode_changed"))
    client.put("/api/permissions/mode", headers=owner, json={"mode": "ask"})
    assert len(events_of(uid, "mode_changed")) == count


def test_auto_needs_a_connected_model(client, owner, monkeypatch):
    r = client.put("/api/permissions/mode", headers=owner, json={"mode": "auto"})
    assert r.status_code == 409 and "model" in r.json()["detail"]
    assert client.get("/api/permissions", headers=owner).json()["mode"] == "ask"
    monkeypatch.setattr(reviewer, "available", lambda: True)
    ok = client.put("/api/permissions/mode", headers=owner, json={"mode": "auto"}).json()
    assert (ok["mode"], ok["mode_label"], ok["auto_available"], ok["auto_effective"]) == ("auto", "Auto", True, True)
    # the provider goes away: auto stays chosen but acts as ask, and says so
    monkeypatch.undo()
    monkeypatch.setattr(settings, "llm_provider", "mock", raising=False)
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    state = client.get("/api/permissions", headers=owner).json()
    assert (state["mode"], state["auto_available"], state["auto_effective"]) == ("auto", False, False)
    assert state["mode_label"] == "Auto (acting as Ask first)"


def test_a_bad_mode_is_rejected(client, owner):
    for body in ({"mode": "yolo"}, {"mode": ""}, {}, {"mode": None}):
        assert client.put("/api/permissions/mode", headers=owner, json=body).status_code == 422


def test_boundaries(client, owner):
    uid = me(client, owner)
    text = "Never email anyone at acme.com.\nAsk before anything on Fridays."
    r = client.put("/api/permissions/boundaries", headers=owner, json={"text": f"  {text}  "})
    assert r.status_code == 200 and r.json()["boundaries"] == text and set(r.json()) == STATE_KEYS
    assert client.get("/api/permissions", headers=owner).json()["boundaries"] == text
    (ev,) = events_of(uid, "boundaries_changed")[-1:]
    assert ev.detail == {"length": len(text)} and "acme" not in str(ev.detail) + ev.target
    n = len(events_of(uid, "boundaries_changed"))
    client.put("/api/permissions/boundaries", headers=owner, json={"text": text})
    assert len(events_of(uid, "boundaries_changed")) == n
    assert client.put("/api/permissions/boundaries", headers=owner, json={"text": "x" * 2001}).status_code == 422
    assert client.put("/api/permissions/boundaries", headers=owner, json={"text": "x" * 2000}).status_code == 200
    assert client.put("/api/permissions/boundaries", headers=owner, json={"text": ""}).json()["boundaries"] == ""


def test_reviewer_model_is_reported_and_settable(client, owner, monkeypatch):
    monkeypatch.setattr(settings, "reviewer_model", "", raising=False)
    monkeypatch.setattr(settings, "background_model", "", raising=False)
    saved = {}
    from app.services import local_config

    monkeypatch.setattr(local_config, "write_values", lambda updates: saved.update(updates))
    monkeypatch.setattr(local_config, "is_writable", lambda: True)
    r = client.put("/api/local/provider", headers=owner, json={"reviewer_model": "careful-model-1"})
    assert r.status_code == 200, r.text
    assert r.json()["reviewer_model"] == "careful-model-1" and saved == {"REVIEWER_MODEL": "careful-model-1"}
    assert client.get("/api/permissions", headers=owner).json()["reviewer_model"] == "careful-model-1"
    assert client.put("/api/local/provider", headers=owner, json={"reviewer_model": "bad model!"}).status_code == 422
    assert client.get("/api/local/provider", headers=owner).json()["reviewer_model"] == "careful-model-1"
    monkeypatch.setattr(settings, "reviewer_model", "", raising=False)
    monkeypatch.setattr(settings, "background_model", "small-bg", raising=False)
    assert client.get("/api/permissions", headers=owner).json()["reviewer_model"] == "small-bg"


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def test_catalogue(client, owner):
    rows = client.get("/api/permissions/catalogue", headers=owner).json()
    assert len(rows) == 60 and all(set(r) == {"tool", "system", "kind", "targets", "always_user"} for r in rows)
    by = {r["tool"]: r for r in rows}
    assert by["send_email"] == {"tool": "send_email", "system": "core-inbox", "kind": "send", "targets": ["recipient"], "always_user": False}
    assert by["delete_file"]["always_user"] is True and by["delete_file"]["targets"] == ["path"]
    assert by["fetch_url"]["targets"] == ["domain"] and by["list_tasks"]["targets"] == []
    assert set(by) == {n for n, p in catalogue.CATALOGUE.items() if not p.loop_internal}
    assert all(t in ("path", "command", "recipient", "domain", "app") for r in rows for t in r["targets"])
    assert client.get("/api/permissions/catalogue").status_code == 401


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def add(client, owner, **body):
    return client.post("/api/permissions/rules", headers=owner, json={"effect": "allow", "system": "core-inbox", **body})


def test_add_list_and_remove_a_rule(client, owner):
    uid = me(client, owner)
    r = add(client, owner, tool="send_email", target_kind="recipient", pattern="  Bob@Example.COM ", note="Bob is my accountant")
    assert r.status_code == 200, r.text
    rule = r.json()
    assert set(rule) == RULE_KEYS
    assert (rule["effect"], rule["system"], rule["tool"], rule["target_kind"], rule["pattern"], rule["source"], rule["applies_to"]) == (
        "allow", "core-inbox", "send_email", "recipient", "bob@example.com", "user", "")
    assert rule["expires_at"] is None and rule["revoked_at"] is None and rule["note"] == "Bob is my accountant" and rule["id"]
    listing = client.get("/api/permissions/rules", headers=owner).json()
    assert [x["id"] for x in listing] == [rule["id"]] and set(listing[0]) == RULE_KEYS
    (ev,) = events_of(uid, "rule_added")[-1:]
    assert (ev.actor, ev.outcome, ev.tool) == ("user", "allow", "send_email") and ev.detail["rule_id"] == rule["id"]
    assert len([e for e in events_of(uid, "rule_added") if e.detail.get("rule_id") == rule["id"]]) == 1, "logged once"
    note = notes_of(uid)[-1]
    assert note.level == "urgent" and "widened" in note.title.lower()

    d = client.delete(f"/api/permissions/rules/{rule['id']}", headers=owner)
    assert (d.status_code, d.json()) == (200, {"revoked": True})
    assert client.get("/api/permissions/rules", headers=owner).json() == []
    assert client.delete(f"/api/permissions/rules/{rule['id']}", headers=owner).status_code == 404
    kept = client.get("/api/permissions/rules?include_expired=true", headers=owner).json()
    mine = [x for x in kept if x["id"] == rule["id"]]
    assert len(mine) == 1 and mine[0]["revoked_at"] is not None
    assert events_of(uid, "rule_revoked")[-1].detail["rule_id"] == rule["id"]
    assert notes_of(uid)[-1].level == "info"


def test_a_block_rule_is_not_a_widening(client, owner):
    uid = me(client, owner)
    assert add(client, owner, effect="block", tool="send_email").status_code == 200
    assert notes_of(uid)[-1].level == "info"


def test_a_rule_works_in_the_gate_and_stops_when_removed(client, owner):
    from app.permissions import gate

    uid = me(client, owner)
    rule = add(client, owner, tool="send_email", target_kind="recipient", pattern="bob@example.com").json()

    def verdict(to):
        db = SessionLocal()
        try:
            user = db.get(User, uid)
            d = gate.decide(db, user, "send_email", {"to": to, "subject": "s", "body": "b"},
                            gate.build_context(db, user, "chat"))
            return d.kind, d.reason_code
        finally:
            db.close()

    assert verdict("bob@example.com") == ("allow", "RULE_ALLOW")
    assert verdict("eve@evil.org") == ("ask", "MODE_ASK")
    client.delete(f"/api/permissions/rules/{rule['id']}", headers=owner)
    assert verdict("bob@example.com") == ("ask", "MODE_ASK")
    block = add(client, owner, effect="block", tool="send_email", target_kind="recipient", pattern="@evil.org").json()
    assert verdict("eve@evil.org") == ("deny", "RULE_BLOCK")
    client.delete(f"/api/permissions/rules/{block['id']}", headers=owner)


def test_a_timed_rule_expires(client, owner):
    r = add(client, owner, tool="send_email", minutes=10).json()
    assert r["expires_at"] is not None
    db = SessionLocal()
    try:
        row = db.get(PermissionRule, r["id"])
        row.expires_at = utcnow().replace(year=2020)
        db.commit()
    finally:
        db.close()
    assert client.get("/api/permissions/rules", headers=owner).json() == []
    assert r["id"] in [x["id"] for x in client.get("/api/permissions/rules?include_expired=true", headers=owner).json()]


def test_list_filters(client, owner):
    add(client, owner, tool="send_email")
    add(client, owner, effect="ask", tool="archive_email")
    add(client, owner, effect="block", system="core-calendar")
    ids = lambda q: sorted((x["effect"], x["tool"] or x["system"]) for x in client.get(f"/api/permissions/rules{q}", headers=owner).json())  # noqa: E731
    assert len(ids("")) == 3
    assert ids("?effect=ask") == [("ask", "archive_email")]
    assert ids("?system=core-calendar") == [("block", "core-calendar")]
    assert ids("?tool=send_email") == [("allow", "send_email")]
    assert ids("?effect=block&tool=send_email") == []
    assert client.get("/api/permissions/rules?effect=maybe", headers=owner).status_code == 422


def test_install_rules_are_listed_read_only(client, owner, monkeypatch):
    monkeypatch.setattr(settings, "device_command_denylist", "Format C:, shutdown", raising=False)
    rows = client.get("/api/permissions/rules", headers=owner).json()
    install = [x for x in rows if x["source"] == "install"]
    assert {(x["effect"], x["system"], x["target_kind"], x["pattern"]) for x in install} == {
        ("block", "device-control", "command", "~format c:"), ("block", "device-control", "command", "~shutdown")}
    assert all(x["id"] is None and x["note"] for x in install)
    assert [x["pattern"] for x in client.get("/api/permissions/rules?effect=allow", headers=owner).json()] == []
    assert client.get("/api/permissions/rules?tool=run_command", headers=owner).json() == install
    monkeypatch.setattr(settings, "device_control", "off", raising=False)
    reset_cache()
    assert client.get("/api/permissions/rules", headers=owner).json() == []


@pytest.mark.parametrize("body", [
    {"effect": "maybe"},
    {"system": ""},
    {"system": "no-such-system"},
    {"tool": "no_such_tool"},
    {"tool": "send_email", "system": "core-calendar"},
    {"tool": "send_email", "target_kind": "path", "pattern": "/x"},
    {"tool": "send_email", "target_kind": "recipient"},
    {"tool": "send_email", "pattern": "bob@example.com"},
    {"tool": "send_email", "target_kind": "recipient", "pattern": "not an address"},
    {"tool": "send_email", "target_kind": "bogus", "pattern": "x"},
    {"applies_to": "everything"},
    {"minutes": 0},
    {"minutes": -5},
    {"note": "x" * 301},
    {"tool": "send_email", "effect": "allow", "target_kind": "recipient", "pattern": "@"},
])
def test_bad_rules_are_422(client, owner, body):
    assert add(client, owner, **body).status_code == 422, body
    assert client.get("/api/permissions/rules", headers=owner).json() == []


def test_pattern_validation_per_kind(client, owner):
    # a substring pattern can block or ask, never allow
    assert add(client, owner, system="device-control", tool="run_command", target_kind="command", pattern="~curl").status_code == 422
    ok = add(client, owner, effect="block", system="device-control", tool="run_command", target_kind="command", pattern="~curl | sh")
    assert ok.status_code == 200 and ok.json()["pattern"] == "~curl | sh"
    # a command pattern is one plain command
    assert add(client, owner, system="device-control", tool="run_command", target_kind="command", pattern="git status; rm x").status_code == 422
    cmd = add(client, owner, system="device-control", tool="run_command", target_kind="command", pattern='  git   "status"  ')
    assert cmd.status_code == 200 and cmd.json()["pattern"] == "git status"
    # paths: ** only at the end; domains and apps are normalised
    assert add(client, owner, system="device-control", tool="write_file", target_kind="path", pattern="/a/**/b").status_code == 422
    path = add(client, owner, system="device-control", tool="write_file", target_kind="path", pattern=str(os.path.realpath(os.sep)) + "work" + "/**")
    assert path.status_code == 200
    dom = add(client, owner, system="device-control", tool="fetch_url", target_kind="domain", pattern="HTTPS://Docs.Example.com/")
    assert dom.status_code == 200 and dom.json()["pattern"] == "https://docs.example.com"
    app_rule = add(client, owner, system="device-control", tool="open_app", target_kind="app", pattern="Google Chrome")
    assert app_rule.status_code == 200 and app_rule.json()["pattern"] == "chrome"


def test_a_schedule_scoped_rule(client, owner):
    r = add(client, owner, tool="send_email", applies_to="schedule:abc")
    assert r.status_code == 200 and r.json()["applies_to"] == "schedule:abc"


def test_patch_replaces_the_rule(client, owner):
    uid = me(client, owner)
    old = add(client, owner, effect="ask", tool="send_email", target_kind="recipient", pattern="bob@example.com", note="n").json()
    r = client.patch(f"/api/permissions/rules/{old['id']}", headers=owner, json={"effect": "allow"})
    assert r.status_code == 200, r.text
    new = r.json()
    assert set(new) == RULE_KEYS and new["id"] != old["id"]
    assert (new["effect"], new["tool"], new["target_kind"], new["pattern"], new["note"], new["source"]) == (
        "allow", "send_email", "recipient", "bob@example.com", "n", "user")
    listing = client.get("/api/permissions/rules", headers=owner).json()
    assert [x["id"] for x in listing] == [new["id"]]
    kinds = [(e.kind, e.detail.get("rule_id")) for e in events_of(uid)[-3:]]
    assert ("rule_revoked", old["id"]) in kinds and ("rule_added", new["id"]) in kinds
    assert notes_of(uid)[-1].level == "urgent"
    # pattern (normalised), note and lifetime; the rest stays
    r2 = client.patch(f"/api/permissions/rules/{new['id']}", headers=owner, json={"pattern": "Carol@Example.com", "note": "c", "minutes": 30}).json()
    assert (r2["pattern"], r2["note"], r2["effect"]) == ("carol@example.com", "c", "allow") and r2["expires_at"]
    # without minutes the old lifetime is kept
    r3 = client.patch(f"/api/permissions/rules/{r2['id']}", headers=owner, json={"effect": "block"}).json()
    assert r3["expires_at"] == r2["expires_at"] and r3["effect"] == "block"
    assert notes_of(uid)[-1].level == "info"
    assert client.patch(f"/api/permissions/rules/{old['id']}", headers=owner, json={"effect": "ask"}).status_code == 404


def test_patch_validates(client, owner):
    rule = add(client, owner, effect="block", system="device-control", tool="run_command", target_kind="command", pattern="~curl").json()
    for body in ({}, {"effect": "allow"}, {"pattern": ""}, {"pattern": "a; b", "effect": "ask"}, {"minutes": 0}, {"effect": "nope"}):
        r = client.patch(f"/api/permissions/rules/{rule['id']}", headers=owner, json=body)
        assert r.status_code == 422, body
    assert [x["id"] for x in client.get("/api/permissions/rules", headers=owner).json()] == [rule["id"]], "nothing changed"
    tool_wide = add(client, owner, tool="send_email").json()
    assert client.patch(f"/api/permissions/rules/{tool_wide['id']}", headers=owner, json={"pattern": "x"}).status_code == 422
    assert client.patch("/api/permissions/rules/nope", headers=owner, json={"effect": "ask"}).status_code == 404


def test_one_user_cannot_touch_anothers_rules(client, owner, monkeypatch):
    rule = add(client, owner, tool="send_email").json()
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    monkeypatch.setattr(settings, "device_control", "off", raising=False)
    reset_cache()
    demo = client.post("/api/auth/demo", json={"name": "Other", "email": "other-perm-api@example.com", "timezone": "UTC"})
    assert demo.status_code == 200, demo.text
    o = bearer(demo)
    assert client.get("/api/permissions/rules", headers=o).json() == []
    assert client.delete(f"/api/permissions/rules/{rule['id']}", headers=o).status_code == 404
    assert client.patch(f"/api/permissions/rules/{rule['id']}", headers=o, json={"effect": "ask"}).status_code == 404
    assert client.get("/api/permissions/events", headers=o).json() is not None
    mine = client.put("/api/permissions/mode", headers=o, json={"mode": "skip"})
    assert mine.status_code == 200
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    reset_cache()
    assert client.get("/api/permissions", headers=owner).json()["mode"] == "ask"


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


def test_events_list_page_and_verify(client, owner):
    uid = me(client, owner)
    for i in range(5):
        add(client, owner, tool="send_email", note=f"n{i}")
    batch = client.get("/api/permissions/events?limit=3", headers=owner).json()
    assert len(batch) == 3 and all(set(e) == EVENT_KEYS for e in batch)
    seqs = [e["seq"] for e in batch]
    assert seqs == sorted(seqs, reverse=True), "newest first"
    older = client.get(f"/api/permissions/events?limit=3&before_seq={seqs[-1]}", headers=owner).json()
    assert older and all(e["seq"] < seqs[-1] for e in older)
    assert batch[0]["kind"] == "rule_added" and batch[0]["actor"] == "user" and batch[0]["outcome"] == "allow"
    assert client.get("/api/permissions/events?limit=201", headers=owner).status_code == 422
    assert client.get("/api/permissions/events?limit=0", headers=owner).status_code == 422
    assert client.get("/api/permissions/events?before_seq=0", headers=owner).status_code == 422
    v = client.get("/api/permissions/events/verify", headers=owner).json()
    assert v["ok"] is True and v["first_bad_seq"] is None and v["checked"] == len(events_of(uid))
    assert set(v) == {"ok", "checked", "first_bad_seq"}


def test_verify_finds_an_edited_row(client, owner):
    uid = me(client, owner)
    add(client, owner, tool="send_email")
    add(client, owner, tool="archive_email")
    rows = events_of(uid)
    target = rows[-2]
    db = SessionLocal()
    try:
        db.execute(update(PermissionEvent).where(PermissionEvent.id == target.id).values(outcome="deny"))
        db.commit()
    finally:
        db.close()
    v = client.get("/api/permissions/events/verify", headers=owner).json()
    assert v["ok"] is False and v["first_bad_seq"] == target.seq and v["checked"] == target.seq - 1
    db = SessionLocal()
    try:
        db.execute(update(PermissionEvent).where(PermissionEvent.id == target.id).values(outcome=target.outcome))
        db.commit()
    finally:
        db.close()
    assert client.get("/api/permissions/events/verify", headers=owner).json()["ok"] is True


def test_the_legacy_mapping_shows_up_in_the_log(client, owner):
    uid = me(client, owner)
    db = SessionLocal()
    try:
        user = db.get(User, uid)
        db.add(ApprovalGrant(user_id=uid, tool_name="open_app", scope="always"))
        db.commit()
        apply_legacy(db, user, "relaxed")
    finally:
        db.close()
    first = client.get("/api/permissions/events", headers=owner).json()
    mapped = [e for e in first if e["kind"] == "legacy_mapped"]
    assert len(mapped) == 1 and mapped[0]["actor"] == "migration"
    assert mapped[0]["detail"]["tier"] == "relaxed" and mapped[0]["detail"]["mode"] == "ask"
    again = client.get("/api/permissions/events", headers=owner).json()
    assert len([e for e in again if e["kind"] == "legacy_mapped"]) == 1, "written once"
    assert client.get("/api/permissions/events/verify", headers=owner).json()["ok"] is True
    rules = client.get("/api/permissions/rules", headers=owner).json()
    assert rules and {r["source"] for r in rules} == {"legacy"}
    # a legacy rule can be revoked like any other
    assert client.delete(f"/api/permissions/rules/{rules[0]['id']}", headers=owner).status_code == 200


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------


def changing_routes():
    return [
        ("PUT", "/api/permissions/mode", {"mode": "ask"}),
        ("PUT", "/api/permissions/boundaries", {"text": "x"}),
        ("POST", "/api/permissions/rules", {"effect": "allow", "system": "core-inbox", "tool": "send_email"}),
        ("PATCH", "/api/permissions/rules/some-id", {"effect": "ask"}),
        ("DELETE", "/api/permissions/rules/some-id", None),
    ]


def remote(client, h):
    uid = me(client, h)
    db = SessionLocal()
    try:
        device = PairedDevice(user_id=uid, name="phone")
        db.add(device)
        db.commit()
        did = device.id
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token(uid, {'typ': 'remote', 'did': did})}"}


def test_there_is_no_keyless_sign_in_to_change_the_mode_with(client, owner):
    assert sign_in(client, None, configured="").status_code == 403
    assert sign_in(client, None).status_code == 403
    from app.security import create_access_token

    keyless = {"Authorization": "Bearer " + create_access_token(me(client, owner), {"amr": "password"})}
    for method, path, body in changing_routes():
        r = client.request(method, path, headers=keyless, json=body)
        assert r.status_code == 401, (path, r.status_code)
    assert client.get("/api/permissions", headers=owner).json()["mode"] == "ask"


def test_remote_cannot_change_mode(client, owner):
    phone = remote(client, owner)
    for method, path, body in changing_routes():
        assert client.request(method, path, headers=phone, json=body).status_code == 403, path
    assert client.get("/api/permissions", headers=phone).status_code == 200, "a paired phone may look"


def test_a_process_aura_started_cannot_change_mode(client, owner, monkeypatch):
    from types import SimpleNamespace

    fake = SimpleNamespace(net_connections=lambda kind: [SimpleNamespace(laddr=SimpleNamespace(port=50000), pid=os.getpid())],
                           Process=lambda pid: SimpleNamespace(pid=pid, parents=lambda: [], environ=lambda: {}, name=lambda: "python"))
    monkeypatch.setattr(deps, "_load_psutil", lambda: fake)
    for method, path, body in changing_routes():
        assert client.request(method, path, headers=owner, json=body).status_code == 403, path
    assert any(e.kind == "self_change_blocked" for e in events_of(me(client, owner)))


def test_hosted_sessions_may_change_their_own_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    monkeypatch.setattr(settings, "device_control", "off", raising=False)
    reset_cache()
    demo = client.post("/api/auth/demo", json={"name": "Hosted", "email": "hosted-perm-api@example.com", "timezone": "UTC"})
    h = bearer(demo)
    assert client.put("/api/permissions/mode", headers=h, json={"mode": "skip"}).json()["mode"] == "skip"
    assert client.post("/api/permissions/rules", headers=h, json={"effect": "ask", "system": "core-inbox"}).status_code == 200
    assert client.get("/api/permissions/events/verify", headers=h).json()["ok"] is True


def test_every_change_is_logged_and_notified(client, owner):
    uid = me(client, owner)
    start_events, start_notes = len(events_of(uid)), len(notes_of(uid))
    client.put("/api/permissions/mode", headers=owner, json={"mode": "skip"})
    client.put("/api/permissions/boundaries", headers=owner, json={"text": "no Fridays"})
    rule = add(client, owner, tool="send_email").json()
    client.patch(f"/api/permissions/rules/{rule['id']}", headers=owner, json={"effect": "ask"})
    rid = client.get("/api/permissions/rules", headers=owner).json()[0]["id"]
    client.delete(f"/api/permissions/rules/{rid}", headers=owner)
    new_events = [e.kind for e in events_of(uid)[start_events:]]
    assert new_events == ["mode_changed", "boundaries_changed", "rule_added", "rule_added", "rule_revoked", "rule_revoked"]
    assert len(notes_of(uid)) - start_notes == 5
    assert all(e.actor == "user" for e in events_of(uid)[start_events:])


# ---------------------------------------------------------------------------
# The old routes and settings
# ---------------------------------------------------------------------------


def test_grant_routes_are_gone(client, owner):
    assert client.get("/api/grants", headers=owner).status_code in (404, 405)
    assert client.delete("/api/grants/send_email", headers=owner).status_code in (404, 405)


def test_autonomy_level_rejected(client, owner):
    r = client.patch("/api/settings", headers=owner, json={"autonomy_level": "full"})
    assert r.status_code == 422 and "/api/permissions/mode" in r.text
    ok = client.patch("/api/settings", headers=owner, json={"theme": "dark"})
    assert ok.status_code == 200 and "autonomy_level" not in ok.json()
    assert "autonomy_level" not in client.get("/api/settings", headers=owner).json()
    assert client.get("/api/permissions", headers=owner).json()["mode"] == "ask"


def test_onboarding_and_the_loop_no_longer_read_the_old_level():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"autonomy_level|legacy_tier|permissions import.*interim|from app\.agent(\.| import )autonomy|agent\.autonomy", text):
            offenders.append(str(path.relative_to(root)))
    allowed = {"models.py", "migrate_legacy.py", "migration_ops.py", "config.py", "schemas.py"}  # the column, the 1.0 snapshot, a setting, the 422 message
    assert [o for o in offenders if o not in allowed and not o.startswith("agent/autonomy.py")] == []
