"""The device routes the web client uses: folders, apps, blocklist, screen control (spec 10 section 11, contract note 11)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.device import windows  # noqa: E402
from app.device.runtime import reset_cache  # noqa: E402
from app.main import app  # noqa: E402
from app.models import DeviceAppGrant, DeviceFolder, User  # noqa: E402
from app.permissions import access  # noqa: E402
from tests.perm_helpers import apply_legacy  # noqa: E402
from tests.schema import drop_test_schema, ensure_test_schema  # noqa: E402


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
    monkeypatch.setattr(settings, "aura_home", str(tmp_path / "aura-home"), raising=False)
    monkeypatch.setattr(settings, "device_allowed_roots", "", raising=False)
    reset_cache()
    access.reset_state()
    yield
    reset_cache()


@pytest.fixture
def h(client):
    saved = settings.aura_local_login_key
    settings.aura_local_login_key = "k"
    try:
        r = client.post("/api/auth/local", json={"key": "k"})
    finally:
        settings.aura_local_login_key = saved
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_folders_round_trip(client, h, tmp_path):
    work = tmp_path / "Finance"
    work.mkdir()
    r = client.post("/api/device/folders", headers=h, json={"path": str(work), "can_delete": False})
    assert r.status_code == 200, r.text
    f = r.json()
    assert set(f) >= {"id", "path", "can_delete", "source", "within_install_limit", "created_at"}
    assert f["path"] == os.path.realpath(str(work)) and f["can_delete"] is False and f["source"] == "user"
    assert f["within_install_limit"] is True
    again = client.post("/api/device/folders", headers=h, json={"path": str(work), "can_delete": False}).json()
    assert again["id"] == f["id"]
    assert client.patch(f"/api/device/folders/{f['id']}", headers=h, json={"can_delete": True}).json()["can_delete"] is True
    assert f["id"] in [x["id"] for x in client.get("/api/device/folders", headers=h).json()]
    assert client.delete(f"/api/device/folders/{f['id']}", headers=h).status_code == 200
    assert f["id"] not in [x["id"] for x in client.get("/api/device/folders", headers=h).json()]
    assert client.delete(f"/api/device/folders/{f['id']}", headers=h).status_code == 404
    # connecting it again brings the same row back
    back = client.post("/api/device/folders", headers=h, json={"path": str(work), "can_delete": True}).json()
    assert back["id"] == f["id"] and back["can_delete"] is True


def test_folder_validation(client, h, tmp_path, monkeypatch):
    bad = [str(tmp_path / "missing"), os.path.abspath(os.sep), str(tmp_path / "aura-home"),
           os.path.expanduser("~/.ssh")]
    (tmp_path / "aura-home").mkdir()
    for path in bad:
        r = client.post("/api/device/folders", headers=h, json={"path": path})
        assert r.status_code == 422, path
    limit = tmp_path / "limit"
    limit.mkdir()
    monkeypatch.setattr(settings, "device_allowed_roots", str(limit), raising=False)
    assert client.post("/api/device/folders", headers=h, json={"path": str(tmp_path)}).status_code == 422
    assert client.post("/api/device/folders", headers=h, json={"path": str(limit)}).status_code == 200
    listing = client.get("/api/device/folders", headers=h).json()
    assert all("within_install_limit" in x for x in listing)


def test_legacy_roots_are_listed_and_changeable(client, h, tmp_path, monkeypatch):
    root = tmp_path / "legacy-root"
    root.mkdir()
    db = SessionLocal()
    try:
        user = db.scalars(select(User).where(User.email.like("%@localhost"))).first()
        for row in db.scalars(select(DeviceFolder).where(DeviceFolder.user_id == user.id)).all():
            db.delete(row)
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(settings, "device_allowed_roots", str(root), raising=False)
    db = SessionLocal()
    try:
        apply_legacy(db, db.scalars(select(User).where(User.email.like("%@localhost"))).first())
    finally:
        db.close()
    listing = client.get("/api/device/folders", headers=h).json()
    assert [x["path"] for x in listing] == [os.path.realpath(str(root))]
    item = listing[0]
    assert item["source"] == "legacy" and item["can_delete"] is True
    r = client.patch(f"/api/device/folders/{item['id']}", headers=h, json={"can_delete": False})
    assert r.status_code == 200 and r.json()["can_delete"] is False and r.json()["id"] == item["id"]
    assert client.delete(f"/api/device/folders/{r.json()['id']}", headers=h).status_code == 200
    assert client.get("/api/device/folders", headers=h).json() == []


def test_apps_grants_and_blocklist(client, h, monkeypatch):
    monkeypatch.setattr(windows, "running_apps", lambda: [{"app_id": "chrome", "display_name": "Google Chrome"}])
    data = client.get("/api/device/apps", headers=h).json()
    assert set(data) == {"grants", "blocks", "running"} and data["running"] == [{"app_id": "chrome", "display_name": "Google Chrome"}]
    assert any(b["app_id"] == "bank" and b["match"] == "keyword" and b["source"] == "default" for b in data["blocks"])
    r = client.put("/api/device/apps/Google%20Chrome", headers=h, json={"tier": "click"})
    assert r.status_code == 200 and r.json()["app_id"] == "chrome" and r.json()["tier"] == "click"
    assert r.json()["display_name"] == "Google Chrome"
    assert client.put("/api/device/apps/chrome", headers=h, json={"tier": "full"}).json()["tier"] == "full"
    # terminals and editors stop at click; blocked apps cannot be granted
    assert client.put("/api/device/apps/vscode", headers=h, json={"tier": "full"}).status_code == 422
    assert client.put("/api/device/apps/vscode", headers=h, json={"tier": "click"}).status_code == 200
    assert client.put("/api/device/apps/1Password", headers=h, json={"tier": "view"}).status_code == 422
    assert client.put("/api/device/apps/chrome", headers=h, json={"tier": "root"}).status_code == 422
    assert [g["app_id"] for g in client.get("/api/device/apps", headers=h).json()["grants"]] == ["chrome", "vscode"]
    # blocking an app ends its grant; unblocking is the user's choice and stays
    b = client.post("/api/device/blocklist", headers=h, json={"app_id": "Google Chrome", "match": "app"})
    assert b.status_code == 200 and b.json()["app_id"] == "chrome" and b.json()["source"] == "user"
    assert [g["app_id"] for g in client.get("/api/device/apps", headers=h).json()["grants"]] == ["vscode"]
    assert client.put("/api/device/apps/chrome", headers=h, json={"tier": "view"}).status_code == 422
    assert client.delete(f"/api/device/blocklist/{b.json()['id']}", headers=h).status_code == 200
    assert client.delete(f"/api/device/blocklist/{b.json()['id']}", headers=h).status_code == 404
    assert client.delete("/api/device/apps/vscode", headers=h).status_code == 200
    assert client.delete("/api/device/apps/vscode", headers=h).status_code == 404
    # a default removed once stays removed
    default = next(x for x in client.get("/api/device/apps", headers=h).json()["blocks"] if x["app_id"] == "wallet")
    assert client.delete(f"/api/device/blocklist/{default['id']}", headers=h).status_code == 200
    assert all(x["app_id"] != "wallet" for x in client.get("/api/device/apps", headers=h).json()["blocks"])


def test_screen_control_switch_and_status(client, h):
    status = client.get("/api/device", headers=h).json()
    assert status["screen_control_user"] in (True, False) and "presence_accepted" in status
    assert client.put("/api/device/screen-control", headers=h, json={"enabled": False}).json()["enabled"] is False
    assert client.get("/api/device", headers=h).json()["screen_control_user"] is False
    assert client.put("/api/device/screen-control", headers=h, json={"enabled": True}).status_code == 200
    assert client.get("/api/device", headers=h).json()["screen_control_user"] is True


def test_listing_files_uses_the_connected_folders(client, h, tmp_path):
    folder = tmp_path / "visible"
    folder.mkdir()
    (folder / "a.txt").write_text("x")
    other = tmp_path / "hidden"
    other.mkdir()
    client.post("/api/device/folders", headers=h, json={"path": str(folder)})
    ok = client.get("/api/device/files", headers=h, params={"path": str(folder)})
    assert ok.status_code == 200 and ok.json()["entries"][0]["name"] == "a.txt"
    assert client.get("/api/device/files", headers=h, params={"path": str(other)}).status_code == 400
