"""The desktop app, and the doors around a local install.

What's pinned here:

* the web app is served from the API's own address, and nothing outside its
  folder can be reached through it;
* a local install answers only to its own name (DNS rebinding), and to other
  devices only once they've paired — and a paired phone can't widen AURA's
  limits, run the console or pair more devices;
* the owner sign-in needs the key from the app's own link when one is set;
* the in-app model settings write the settings file and never echo a key;
* the launcher keeps AURA's data and code off-limits to its own file tools;
* app names and notification text from the model can't smuggle in commands.

Run from backend/:  pytest -q tests/test_desktop.py
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

# Must match test_smoke.py exactly — see the note in test_password_auth.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import envfile, web  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import PairedDevice, PairingCode, User  # noqa: E402

OWNER_KEY = "test-local-login-key-0123456789"


@pytest.fixture(scope="module", autouse=True)
def schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def local(monkeypatch, tmp_path):
    """A local install: local mode on, its own settings file, a login key."""
    config = tmp_path / "aura.env"
    config.write_text("# test settings\n", encoding="utf-8")
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "aura_config_file", str(config), raising=False)
    monkeypatch.setattr(settings, "aura_local_login_key", OWNER_KEY, raising=False)
    monkeypatch.setattr(settings, "aura_home", str(tmp_path / "home"), raising=False)
    monkeypatch.setattr(settings, "remote_access", False, raising=False)
    return config


@pytest.fixture
def here(as_peer):
    """This computer, using its own address."""
    with TestClient(as_peer(app), base_url="http://127.0.0.1:8765") as c:
        yield c


@pytest.fixture
def phone(as_peer):
    """Another device on the network."""
    return TestClient(as_peer(app, "192.168.1.77"), base_url="http://192.168.1.20:8765")


def _owner_token(client) -> str:
    resp = client.post("/api/auth/local", json={"key": OWNER_KEY})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Serving the web app
# ---------------------------------------------------------------------------


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "out"
    (root / "_next" / "static" / "chunks").mkdir(parents=True)
    (root / "index.html").write_text("<html>home</html>")
    (root / "chat.html").write_text("<html>chat</html>")
    (root / "404.html").write_text("<html>missing</html>")
    (root / "chat.txt").write_text("rsc payload")
    (root / "_next" / "static" / "chunks" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("not for the web")
    served = FastAPI()

    @served.get("/api/ping")
    def ping():
        return {"pong": True}

    assert web.install(served, str(root))
    return TestClient(served)


def test_pages_are_found_the_way_next_exports_them(site):
    assert site.get("/").text == "<html>home</html>"
    assert site.get("/chat").text == "<html>chat</html>"
    assert site.get("/chat.txt").text == "rsc payload"
    missing = site.get("/no-such-page")
    assert missing.status_code == 404 and "missing" in missing.text


def test_scripts_are_served_as_javascript_and_cached_for_good(site):
    resp = site.get("/_next/static/chunks/app.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/javascript")
    assert "immutable" in resp.headers["cache-control"]
    assert site.get("/chat").headers["cache-control"] == "no-cache"


def test_nothing_outside_the_web_folder_is_reachable(site):
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/_next/../../secret.txt"):
        resp = site.get(path)
        assert "not for the web" not in resp.text, path


def test_api_paths_never_fall_through_to_the_web_app(site):
    assert site.get("/api/ping").json() == {"pong": True}
    resp = site.get("/api/does-not-exist")
    assert resp.status_code == 404 and "missing" not in resp.text


def test_install_refuses_a_folder_without_an_index(tmp_path):
    assert web.install(FastAPI(), str(tmp_path)) is False


# ---------------------------------------------------------------------------
# Who may talk to a local install
# ---------------------------------------------------------------------------


def test_a_rebound_domain_is_refused_even_from_this_computer(local, here):
    resp = here.get("/api/auth/config", headers={"Host": "attacker.example:8765"})
    assert resp.status_code == 421
    assert here.get("/api/auth/config").status_code == 200
    assert here.get("/api/auth/config", headers={"Host": "localhost:8765"}).status_code == 200


def test_the_host_check_is_off_on_a_hosted_deployment(here, monkeypatch):
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    resp = here.get("/api/auth/config", headers={"Host": "aura-ai-psi-self.vercel.app"})
    assert resp.status_code == 200


def test_other_devices_are_refused_while_remote_access_is_off(local, phone):
    for path in ("/", "/api/auth/config", "/api/auth/me"):
        resp = phone.get(path)
        assert resp.status_code == 403, path
        assert "only answers to this computer" in resp.json()["detail"]


def test_the_owner_sign_in_needs_the_key_from_the_apps_link(local, here):
    assert here.post("/api/auth/local").status_code == 403
    assert here.post("/api/auth/local", json={"key": "wrong"}).status_code == 403
    token = _owner_token(here)
    me = here.get("/api/auth/me", headers=_bearer(token))
    assert me.status_code == 200 and me.json()["email"].endswith("@localhost")
    config = here.get("/api/auth/config").json()
    assert config["local_login_key_required"] is True
    assert config["remote_client"] is False


def test_a_session_token_is_no_good_from_the_network(local, here, phone, monkeypatch):
    token = _owner_token(here)
    monkeypatch.setattr(settings, "remote_access", True, raising=False)
    resp = phone.get("/api/auth/me", headers=_bearer(token))
    assert resp.status_code == 401


def test_a_request_naming_no_host_is_refused(local):
    """No Host header names nothing — so it doesn't name this computer either."""
    import asyncio

    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.0",
        "method": "GET", "scheme": "http", "path": "/api/auth/config",
        "raw_path": b"/api/auth/config", "query_string": b"", "root_path": "",
        "headers": [], "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8765),
    }
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 421


# ---------------------------------------------------------------------------
# One account: the owner's
# ---------------------------------------------------------------------------
#
# Anything that can reach 127.0.0.1 — another user of a shared computer, any
# program on it — could otherwise sign up, take a session, and use the shell
# through it. The owner sign-in needs the key from the app's link; nothing
# else may make a session here.


def test_there_is_no_other_way_to_make_an_account_here(local, here):
    attempts = {
        "/api/auth/register": {"email": "intruder@example.com", "password": "correct horse battery", "name": "x"},
        "/api/auth/login": {"email": "intruder@example.com", "password": "correct horse battery"},
        "/api/auth/demo": {"email": "intruder@example.com", "name": "x"},
        "/api/auth/password/reset": {"token": "x" * 20, "password": "correct horse battery"},
    }
    for path, body in attempts.items():
        resp = here.post(path, json=body)
        assert resp.status_code == 403, (path, resp.status_code, resp.text)
        assert "Open AURA on this computer" in resp.json()["detail"]
    config = here.get("/api/auth/config").json()
    assert config["password_login_enabled"] is False
    assert config["demo_login_enabled"] is False


def test_an_account_other_than_the_owners_gets_nothing_here(local, here, monkeypatch):
    """An account that exists anyway — made before, or behind AURA's back."""
    from app.security import create_access_token

    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    db = SessionLocal()
    try:
        other = db.query(User).filter(User.email == "someone-else@example.com").first()
        if other is None:
            other = User(email="someone-else@example.com", name="Someone Else")
            db.add(other)
            db.commit()
            db.refresh(other)
        token = create_access_token(other.id)
    finally:
        db.close()

    for method, path, body in (
        ("GET", "/api/auth/me", None),
        ("POST", "/api/device/run", {"command": "echo should-not-run"}),
        ("GET", "/api/local/provider", None),
        ("PUT", "/api/local/provider", {"openai_base_url": "https://attacker.example/v1"}),
        ("POST", "/api/remote/pairing", None),
    ):
        resp = here.request(method, path, headers=_bearer(token), json=body)
        assert resp.status_code == 401, (path, resp.status_code, resp.text)

    # The owner, meanwhile, is unaffected.
    assert here.get("/api/auth/me", headers=_bearer(_owner_token(here))).status_code == 200


def test_google_can_connect_but_not_sign_anyone_in_here(local, here, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "client-id", raising=False)
    monkeypatch.setattr(settings, "google_client_secret", "client-secret", raising=False)
    assert here.get("/api/auth/google/start").status_code == 403
    resp = here.get("/api/auth/google/start", headers=_bearer(_owner_token(here)))
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "connect"


# ---------------------------------------------------------------------------
# Pairing a phone
# ---------------------------------------------------------------------------


def _start_pairing(here, token) -> dict:
    resp = here.post("/api/remote/pairing", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_pairing_end_to_end(local, here, phone, monkeypatch):
    monkeypatch.setattr(settings, "remote_access", True, raising=False)
    owner = _owner_token(here)

    # Before pairing, the phone sees the app and the pairing form — nothing else.
    assert phone.get("/api/auth/config").json()["remote_client"] is True
    assert phone.get("/api/auth/me").status_code == 401

    pairing = _start_pairing(here, owner)
    code = pairing["code"]
    assert len(code.replace("-", "")) == 8

    paired = phone.post("/api/remote/pair", json={"code": code.lower(), "device_name": "Pixel"})
    assert paired.status_code == 200, paired.text
    phone_token = paired.json()["access_token"]

    # The phone uses AURA…
    assert phone.get("/api/auth/me", headers=_bearer(phone_token)).status_code == 200
    status = phone.get("/api/remote/status", headers=_bearer(phone_token)).json()
    assert status["remote_session"] is True

    # …but can't widen its limits, use the console, or pair more devices.
    assert phone.get("/api/local/provider", headers=_bearer(phone_token)).status_code == 403
    assert phone.post("/api/remote/pairing", headers=_bearer(phone_token)).status_code == 403
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    console = phone.post(
        "/api/device/run", headers=_bearer(phone_token), json={"command": "echo hi"}
    )
    assert console.status_code == 403

    # A code works once.
    again = phone.post("/api/remote/pair", json={"code": code, "device_name": "Second"})
    assert again.status_code == 400

    # Unpairing on the computer signs the phone out at once.
    devices = here.get("/api/remote/devices", headers=_bearer(owner)).json()
    pixel = next(d for d in devices if d["name"] == "Pixel")
    assert here.delete(f"/api/remote/devices/{pixel['id']}", headers=_bearer(owner)).status_code == 200
    assert phone.get("/api/auth/me", headers=_bearer(phone_token)).status_code == 401


def test_five_wrong_codes_end_the_pairing(local, here, phone, monkeypatch):
    monkeypatch.setattr(settings, "remote_access", True, raising=False)
    owner = _owner_token(here)
    code = _start_pairing(here, owner)["code"]
    for _ in range(5):
        assert phone.post("/api/remote/pair", json={"code": "AAAA-AAAA"}).status_code == 400
    resp = phone.post("/api/remote/pair", json={"code": code})
    assert resp.status_code == 400


def test_turning_remote_access_off_stops_paired_devices_at_once(local, here, phone, monkeypatch):
    monkeypatch.setattr(settings, "remote_access", True, raising=False)
    owner = _owner_token(here)
    code = _start_pairing(here, owner)["code"]
    phone_token = phone.post("/api/remote/pair", json={"code": code}).json()["access_token"]

    resp = here.put("/api/local/remote", headers=_bearer(owner), json={"enabled": False})
    assert resp.status_code == 200
    assert "REMOTE_ACCESS=false" in local.read_text()
    assert phone.get("/api/auth/me", headers=_bearer(phone_token)).status_code == 403


def test_pairing_needs_remote_access_on(local, here):
    owner = _owner_token(here)
    assert here.post("/api/remote/pairing", headers=_bearer(owner)).status_code == 404


def test_only_a_hash_of_the_code_is_stored(local, here, monkeypatch):
    monkeypatch.setattr(settings, "remote_access", True, raising=False)
    owner = _owner_token(here)
    code = _start_pairing(here, owner)["code"]
    db = SessionLocal()
    try:
        rows = db.query(PairingCode).all()
        assert rows and all(code.replace("-", "") not in r.code_hash for r in rows)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Model settings on this computer
# ---------------------------------------------------------------------------


def test_a_key_is_saved_applied_and_never_shown_back(local, here, monkeypatch):
    from app import llm

    monkeypatch.setattr(settings, "llm_provider", "auto", raising=False)
    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    monkeypatch.setattr(settings, "openai_base_url", "", raising=False)
    owner = _owner_token(here)
    key = "sk-ant-api03-" + "x" * 40 + "WXYZ"
    try:
        resp = here.put(
            "/api/local/provider",
            headers=_bearer(owner),
            json={"anthropic_api_key": key, "anthropic_model": "claude-sonnet-5-5"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["provider"] == "anthropic"
        assert body["anthropic_key_set"] is True
        assert key not in resp.text and body["anthropic_key_hint"].endswith("WXYZ")
        assert envfile.read_values(local)["ANTHROPIC_API_KEY"] == key

        # Removing it goes back to the offline mode.
        resp = here.put("/api/local/provider", headers=_bearer(owner), json={"anthropic_api_key": ""})
        assert resp.json()["provider"] == "mock"
        assert "ANTHROPIC_API_KEY" not in envfile.read_values(local)
    finally:
        llm.get_provider(force_reload=True)


def test_bad_model_settings_are_refused_before_anything_is_written(local, here):
    owner = _owner_token(here)
    before = local.read_text()
    bad = here.put(
        "/api/local/provider", headers=_bearer(owner),
        json={"anthropic_model": "claude; rm -rf /", "openai_base_url": "file:///etc"},
    )
    assert bad.status_code == 422
    assert local.read_text() == before


def test_model_settings_dont_exist_on_a_hosted_deployment(here):
    from app.security import create_access_token

    db = SessionLocal()
    try:
        user = db.query(User).first()
        token = create_access_token(user.id) if user else ""
    finally:
        db.close()
    if token:
        assert here.get("/api/local/provider", headers=_bearer(token)).status_code == 404


# ---------------------------------------------------------------------------
# The settings file and the launcher
# ---------------------------------------------------------------------------


def test_the_settings_file_keeps_comments_and_order(tmp_path):
    path = tmp_path / "aura.env"
    path.write_text("# mine\nB=2\nA=1 # note\n")
    envfile.write_values({"A": "one two", "C": "3", "B": None}, path)
    assert path.read_text().splitlines()[0] == "# mine"
    assert envfile.read_values(path) == {"A": "one two", "C": "3"}
    with pytest.raises(ValueError):
        envfile.write_values({"A": "line\nbreak"}, path)
    with pytest.raises(ValueError):
        envfile.write_values({"lower": "x"}, path)


def test_the_launcher_makes_its_folder_and_secrets_once(tmp_path):
    import aura_desktop

    home = tmp_path / "home"
    config = aura_desktop.ensure_home(home)
    first = envfile.read_values(config)
    assert all(first.get(name) for name in aura_desktop.GENERATED)
    assert (home / "logs").is_dir() and (home / "commands").is_dir()

    aura_desktop.ensure_home(home)
    assert envfile.read_values(config) == first  # nothing regenerated


def test_the_launcher_fences_off_auras_own_data_and_code(tmp_path):
    import aura_desktop

    home = tmp_path / "home"
    config = aura_desktop.ensure_home(home)
    with mock.patch.dict(os.environ, {"LOCAL_MODE": "", "ANTHROPIC_MODEL": "kept-from-env"}):
        os.environ.pop("DEVICE_EXTRA_PROTECTED_PATHS", None)
        aura_desktop.apply_environment(home, config, 8765, "127.0.0.1", None)
        protected = os.environ["DEVICE_EXTRA_PROTECTED_PATHS"].split(",")
        assert str(home) in protected
        assert str(aura_desktop.install_dir()) in protected
        assert os.environ["LOCAL_MODE"] == "true"
        assert os.environ["ALLOW_DEMO_LOGIN"] == "false"
        assert os.environ["DATABASE_URL"].endswith("aura.db")
        # The real environment beats the settings file.
        assert os.environ["ANTHROPIC_MODEL"] == "kept-from-env"


def test_version_comparison_for_the_update_notice():
    import aura_desktop

    assert aura_desktop._version_tuple("v1.2.10") > aura_desktop._version_tuple("1.2.9")
    assert aura_desktop._version_tuple("1.0.0") == aura_desktop._version_tuple("v1.0.0")


# ---------------------------------------------------------------------------
# Words from the model stay words
# ---------------------------------------------------------------------------


def test_app_names_cant_carry_commands(monkeypatch):
    from app.device import apps

    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    for name in ("calc&del /q C:\\", "notepad|whoami", "*", "chrome; rm -rf ~", "-a Terminal"):
        assert "error" in apps.open_app(name), name
        assert "error" in apps.close_app(name), name


def test_notification_text_is_quoted_for_powershell():
    from app.device.system import _plain, ps_quote

    assert ps_quote("it's $(Remove-Item x)") == "'it''s $(Remove-Item x)'"
    assert ps_quote("\u2019") == "'\u2019\u2019'"
    assert _plain("line\nbreak\x00") == "line break"


def test_fetch_url_only_reads():
    from app.agent.tools import REGISTRY, ToolContext

    tool = REGISTRY["fetch_url"]
    assert "method" not in tool.spec.parameters["properties"]
    with mock.patch.object(settings, "device_control", "on"):
        result = tool.handler(ToolContext(db=None, user=None), {"url": "https://example.com", "method": "POST"})
    assert "error" in result


def test_the_skill_catalog_is_public_and_complete(here):
    from app.agent import skills as skill_registry

    body = here.get("/api/skills/catalog").json()
    assert body["counts"]["skills"] == len(skill_registry.CATALOG)
    assert body["counts"]["device_skills"] == len(skill_registry.DEVICE_CODES)
    assert body["counts"]["tools"] == len(skill_registry.TOOL_TO_SKILL)
    assert {s["code"] for s in body["skills"]} == set(skill_registry.CATALOG)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = SessionLocal()
    try:
        db.query(PairedDevice).delete()
        db.query(PairingCode).delete()
        db.commit()
    finally:
        db.close()


def test_sending_a_request_anywhere_asks_below_relaxed_trust():
    """Reading a file needs no approval; carrying it to a URL must."""
    from app.agent import autonomy

    assert autonomy.risk_class("read_file") == "read"
    assert autonomy.risk_class("fetch_url") == "external"
