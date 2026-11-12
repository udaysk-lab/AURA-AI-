"""Presence and self-change guards on permission-changing routes (spec 10 section 8.5)."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import deps  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.deps import local_owner_email  # noqa: E402
from app.device.runtime import INSTANCE_ID, reset_cache  # noqa: E402
from app.main import app  # noqa: E402
from app.models import DeviceFolder, Notification, PairedDevice, PermissionEvent, User  # noqa: E402
from app.permissions import access  # noqa: E402
from app.security import create_access_token  # noqa: E402
from tests.schema import drop_test_schema, ensure_test_schema  # noqa: E402

KEY = "presence-test-key"


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
    monkeypatch.setattr(deps, "_generated_local_key", "", raising=False)
    reset_cache()
    access.reset_state()
    yield
    access.reset_state()
    reset_cache()


def sign_in(client, key=None, configured=KEY):
    saved = settings.aura_local_login_key
    settings.aura_local_login_key = configured
    try:
        body = {"key": key} if key is not None else None
        r = client.post("/api/auth/local", json=body) if body else client.post("/api/auth/local")
    finally:
        settings.aura_local_login_key = saved
    return r


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner(client):
    """A session opened with the key: allowed to change permissions."""
    r = sign_in(client, KEY)
    assert r.status_code == 200, r.text
    return bearer(r.json()["access_token"])


def _user_id(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["id"]


def local_open(client):
    """A session opened without a key (nothing configured, none offered)."""
    r = sign_in(client, None, configured="")
    assert r.status_code == 200, r.text
    return bearer(r.json()["access_token"])


def remote(client, owner_headers):
    uid = _user_id(client, owner_headers)
    db = SessionLocal()
    try:
        device = PairedDevice(user_id=uid, name="phone")
        db.add(device)
        db.commit()
        did = device.id
    finally:
        db.close()
    return bearer(create_access_token(uid, {"typ": "remote", "did": did}))


# Routes that change what AURA may do, each with a body that would succeed for the right caller.
def changing_routes(tmp_path):
    return [
        ("PUT", "/api/device/screen-control", {"enabled": True}),
        ("POST", "/api/device/folders", {"path": str(tmp_path), "can_delete": False}),
        ("PUT", "/api/device/apps/notes", {"tier": "view"}),
        ("POST", "/api/device/blocklist", {"app_id": "somebank", "match": "keyword"}),
        ("POST", "/api/plugins/research/install", None),
        ("POST", "/api/device/run", {"command": "echo hi"}),
        ("PUT", "/api/permissions/mode", {"mode": "ask"}),
        ("PUT", "/api/permissions/boundaries", {"text": "no Fridays"}),
        ("POST", "/api/permissions/rules", {"effect": "ask", "system": "core-inbox", "tool": "send_email"}),
        ("PATCH", "/api/permissions/rules/no-such-rule", {"effect": "ask"}),
        ("DELETE", "/api/permissions/rules/no-such-rule", None),
    ]


def call(client, method, path, headers, body):
    return client.request(method, path, headers=headers, json=body)


def test_the_token_says_how_the_session_was_opened(client):
    keyed = sign_in(client, KEY).json()["access_token"]
    assert deps.decode_access_token(keyed)["amr"] == "local-key"
    keyless = sign_in(client, None, configured="").json()["access_token"]
    assert deps.decode_access_token(keyless)["amr"] == "local-open"
    # a configured key is still required
    assert sign_in(client, "wrong").status_code == 403
    # the key made at startup also gives local-key
    deps._generated_local_key = "made-at-start"
    r = sign_in(client, "made-at-start", configured="")
    assert deps.decode_access_token(r.json()["access_token"])["amr"] == "local-key"
    assert deps.decode_access_token(sign_in(client, "nope", configured="").json()["access_token"])["amr"] == "local-open"


def test_local_open_cannot_change_permissions(client, owner, tmp_path):
    keyless = local_open(client)
    for method, path, body in changing_routes(tmp_path):
        r = call(client, method, path, keyless, body)
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert "cannot change permissions" in r.json()["detail"], path
    # reading is fine, and the keyed session is not refused
    assert client.get("/api/device/folders", headers=keyless).status_code == 200
    for method, path, body in changing_routes(tmp_path):
        assert call(client, method, path, owner, body).status_code != 403, path


def test_remote_cannot_change_permissions(client, owner, tmp_path):
    phone = remote(client, owner)
    for method, path, body in changing_routes(tmp_path):
        r = call(client, method, path, phone, body)
        assert r.status_code == 403, (path, r.status_code)


def test_a_token_without_the_claim_counts_as_local_open(client):
    uid = _user_id(client, local_open(client))
    old = bearer(create_access_token(uid))  # made before `amr` existed
    assert client.put("/api/device/screen-control", headers=old, json={"enabled": True}).status_code == 403


def test_hosted_sessions_are_not_affected(client, monkeypatch):
    """Hosted: a signed-in session is enough, whatever its claim."""
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    monkeypatch.setattr(settings, "device_control", "", raising=False)
    reset_cache()
    r = client.post("/api/auth/demo", json={"name": "Demo", "email": "demo-presence@example.com", "timezone": "UTC"})
    assert r.status_code == 200, r.text
    h = bearer(r.json()["access_token"])
    assert client.post("/api/plugins/research/install", headers=h).status_code == 200
    # every device route answers 404 with device control off; nothing else changes
    assert client.get("/api/device/folders", headers=h).status_code == 404
    assert client.get("/api/device/apps", headers=h).status_code == 404
    assert client.put("/api/device/screen-control", headers=h, json={"enabled": True}).status_code == 404
    assert client.post("/api/device/presence", headers=h, json={}).status_code == 404
    assert client.get("/api/device", headers=h).json()["enabled"] is False


def _fake_psutil(pid_for_port, parents=(), environ=None, name="python"):
    def connections(kind):
        return [SimpleNamespace(laddr=SimpleNamespace(port=port), pid=pid) for port, pid in pid_for_port.items()]

    class Proc:
        def __init__(self, pid):
            self.pid = pid

        def parents(self):
            return [Proc(p) for p in parents]

        def environ(self):
            return environ or {}

        def name(self):
            return name

    return SimpleNamespace(net_connections=connections, Process=Proc)


@pytest.mark.parametrize("label,fake", [
    ("the server itself", lambda: _fake_psutil({50000: os.getpid()})),
    ("carries this instance's marker", lambda: _fake_psutil({50000: 31337}, environ={"AURA_SPAWNED_BY": INSTANCE_ID})),
    ("descends from the server", lambda: _fake_psutil({50000: 31337}, parents=(4000, os.getpid()))),
])
def test_spawned_process_refused(client, owner, monkeypatch, tmp_path, label, fake):
    monkeypatch.setattr(deps, "_load_psutil", fake)
    r = client.put("/api/device/screen-control", headers=owner, json={"enabled": True})
    assert r.status_code == 403, label
    assert "started by AURA" in r.json()["detail"]
    db = SessionLocal()
    try:
        uid = _user_id(client, owner)
        kinds = [e.kind for e in db.scalars(select(PermissionEvent).where(PermissionEvent.user_id == uid))]
        assert "self_change_blocked" in kinds
        note = db.scalars(select(Notification).where(Notification.user_id == uid, Notification.level == "urgent")
                          .order_by(Notification.created_at.desc())).first()
        assert note and "tried to change AURA's permissions" in note.title
    finally:
        db.close()
    # answering an approval is guarded the same way, "no" included
    assert client.post("/api/pending-actions/x", headers=owner, json={"decision": "reject"}).status_code == 403


def test_other_processes_and_lookup_failures_pass(client, owner, monkeypatch):
    monkeypatch.setattr(deps, "_load_psutil", lambda: _fake_psutil({50000: 31337}, parents=(1,)))
    assert client.put("/api/device/screen-control", headers=owner, json={"enabled": True}).status_code == 200

    def boom():
        raise PermissionError("access denied")

    broken = _fake_psutil({})
    broken.net_connections = lambda kind: boom()
    monkeypatch.setattr(deps, "_load_psutil", lambda: broken)
    assert client.put("/api/device/screen-control", headers=owner, json={"enabled": True}).status_code == 200
    monkeypatch.setattr(deps, "_load_psutil", lambda: None)
    assert client.put("/api/device/screen-control", headers=owner, json={"enabled": True}).status_code == 200


def test_a_browser_aura_opened_is_not_mistaken_for_aura(client, owner, monkeypatch):
    fake = _fake_psutil({50000: 31337}, parents=(os.getpid(),), name="chrome")
    monkeypatch.setattr(deps, "_load_psutil", lambda: fake)
    assert client.put("/api/device/screen-control", headers=owner, json={"enabled": True}).status_code == 200


def test_cooldown_after_click(client, owner):
    uid = _user_id(client, owner)
    access.note_synthetic_input(uid)
    r = client.put("/api/device/screen-control", headers=owner, json={"enabled": True})
    assert r.status_code == 409 and "own mouse or keyboard" in r.json()["detail"]
    assert client.post("/api/pending-actions/x", headers=owner, json={"decision": "once"}).status_code == 409
    access.reset_state()
    assert client.put("/api/device/screen-control", headers=owner, json={"enabled": True}).status_code == 200


def test_console_requires_presence(client, owner):
    keyless = local_open(client)
    assert client.post("/api/device/run", headers=keyless, json={"command": "echo x"}).status_code == 403
    assert client.post("/api/device/run", headers=remote(client, owner), json={"command": "echo x"}).status_code == 403
    uid = _user_id(client, owner)
    access.note_synthetic_input(uid)
    assert client.post("/api/device/run", headers=owner, json={"command": "echo x"}).status_code == 409


def test_every_change_logged_and_notified(client, owner, tmp_path):
    uid = _user_id(client, owner)
    folder = tmp_path / "work"
    folder.mkdir()
    steps = [
        ("POST", "/api/device/folders", {"path": str(folder), "can_delete": False}, "folder_added", "urgent"),
        ("PUT", "/api/device/apps/notes", {"tier": "click"}, "app_granted", "urgent"),
        ("POST", "/api/device/blocklist", {"app_id": "uniquebroker", "match": "keyword"}, "block_added", "info"),
        ("PUT", "/api/device/screen-control", {"enabled": False}, "system_changed", "info"),
        ("POST", "/api/plugins/research/install", None, "system_changed", "urgent"),
        ("DELETE", "/api/device/apps/notes", None, "app_revoked", "info"),
    ]
    for method, path, body, kind, level in steps:
        db = SessionLocal()
        before_e = len(db.scalars(select(PermissionEvent).where(PermissionEvent.user_id == uid)).all())
        before_n = len(db.scalars(select(Notification).where(Notification.user_id == uid)).all())
        db.close()
        r = call(client, method, path, owner, body)
        assert r.status_code in (200, 204), (path, r.text)
        db = SessionLocal()
        try:
            events = db.scalars(select(PermissionEvent).where(PermissionEvent.user_id == uid)
                                .order_by(PermissionEvent.seq)).all()
            notes = db.scalars(select(Notification).where(Notification.user_id == uid)
                               .order_by(Notification.created_at)).all()
            assert len(events) == before_e + 1 and events[-1].kind == kind and events[-1].actor == "user", path
            assert len(notes) == before_n + 1 and notes[-1].level == level, (path, notes[-1].level)
        finally:
            db.close()
    assert client.get("/api/permissions/events/verify", headers=owner).status_code in (200, 404)


def test_presence_ping_only_from_the_computer_itself(client, owner):
    uid = _user_id(client, owner)
    assert not access.is_present(uid)
    assert client.post("/api/device/presence", headers=owner, json={}).status_code == 204
    assert access.is_present(uid)
    access.reset_state()
    assert client.post("/api/device/presence", headers=remote(client, owner), json={}).status_code == 403
    assert not access.is_present(uid)
    status = client.get("/api/device", headers=owner).json()
    assert status["presence_accepted"] is True and "screen_control_user" in status


def test_generated_key_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "aura_home", str(tmp_path / "home"), raising=False)
    monkeypatch.setattr(settings, "aura_local_login_key", "", raising=False)
    key = deps.ensure_local_key()
    path = tmp_path / "home" / "local-login.key"
    assert key and path.read_text() == key and deps.check_local_key(key) and not deps.check_local_key("x")
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600
    # a configured key is used as is: nothing is generated
    monkeypatch.setattr(settings, "aura_local_login_key", "configured", raising=False)
    deps._generated_local_key = ""
    assert deps.ensure_local_key() == "" and deps.generated_local_key() == ""
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    monkeypatch.setattr(settings, "aura_local_login_key", "", raising=False)
    assert deps.ensure_local_key() == ""


def test_the_owner_route_and_schedules_need_presence(client, owner):
    keyless = local_open(client)
    for method, path, body in (
        ("PUT", "/api/channels/telegram/owner", {"owner_sender": "x", "bridge_owner_only": False}),
        ("POST", "/api/schedules", {"prompt": "p", "natural_language": "daily", "name": "n", "cron": "0 9 * * *"}),
        ("POST", "/api/automations", {"natural_language": "when mail arrives, notify me", "enabled": True}),
        ("PUT", "/api/local/provider", {"background_model": "x"}),
        ("PUT", "/api/local/remote", {"enabled": False, "hostnames": ""}),
        ("POST", "/api/remote/pairing", None),
        ("PATCH", "/api/skills/DV01", {"enabled": False}),
    ):
        r = call(client, method, path, keyless, body)
        assert r.status_code == 403, (path, r.status_code)
        assert "cannot change permissions" in r.json()["detail"], path
