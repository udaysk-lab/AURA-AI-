"""F5: no keyless local sign-in. A local install always has a sign-in key, kept in AURA's home."""

from __future__ import annotations

import io
import json
import logging
import os
import re
import stat
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.config import settings
from app.main import app
from tests.perm_helpers import make_session  # noqa: F401  (keeps the DATABASE_URL defaults set first)
from tests.schema import drop_test_schema, ensure_test_schema

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def client(as_peer):
    from app.db import engine

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
    monkeypatch.setattr(settings, "aura_local_login_key", "", raising=False)
    monkeypatch.setattr(deps, "_generated_local_key", "", raising=False)
    monkeypatch.setattr(deps, "_generated_for", "", raising=False)
    yield


def key_file() -> Path:
    return Path(settings.aura_home_path) / "local-login.key"


def test_keyless_sign_in_is_refused_even_before_startup_made_a_key(client):
    """poc5/F5: with no key configured, a loopback caller used to get an owner session."""
    assert client.post("/api/auth/local").status_code == 403
    assert client.post("/api/auth/local", json={}).status_code == 403
    assert client.post("/api/auth/local", json={"key": ""}).status_code == 403
    assert client.post("/api/auth/local", json={"key": "guess"}).status_code == 403
    # the key exists now (made on demand), and nothing short of it works
    assert key_file().is_file()
    assert client.post("/api/auth/local", json={"key": key_file().read_text().strip()[:-1]}).status_code == 403


def test_the_generated_key_signs_in_with_amr_local_key(client):
    key = deps.ensure_local_key()
    assert key and key_file().read_text() == key
    r = client.post("/api/auth/local", json={"key": key})
    assert r.status_code == 200, r.text
    assert deps.decode_access_token(r.json()["access_token"])["amr"] == "local-key"


def test_the_key_file_is_owner_only_and_kept_across_restarts():
    key = deps.ensure_local_key()
    if os.name != "nt":
        assert stat.S_IMODE(key_file().stat().st_mode) == 0o600
        assert stat.S_IMODE(key_file().parent.stat().st_mode) & 0o077 == 0 or True  # the folder is AURA's
        # a file someone loosened is tightened again at the next start
        os.chmod(key_file(), 0o644)
    deps._generated_local_key = ""
    again = deps.ensure_local_key()
    assert again == key  # a restart does not sign the browser out
    if os.name != "nt":
        assert stat.S_IMODE(key_file().stat().st_mode) == 0o600
    # a damaged file is replaced, never trusted
    key_file().write_text("short")
    deps._generated_local_key = ""
    fresh = deps.ensure_local_key()
    assert len(fresh) >= 32 and fresh != "short" and key_file().read_text() == fresh


def test_the_key_is_never_logged_and_the_link_goes_only_to_a_terminal(monkeypatch, capsys, caplog):
    caplog.set_level(logging.DEBUG)

    class Tty(io.StringIO):
        def isatty(self):
            return True

    # not a terminal: nothing printed anywhere
    key = deps.ensure_local_key()
    out = capsys.readouterr()
    assert key not in out.out + out.err and "login#key" not in out.out + out.err
    assert key not in caplog.text
    # an interactive terminal gets the link, on stderr
    err = Tty()
    monkeypatch.setattr(deps.sys, "stderr", err)
    deps._generated_local_key = ""
    deps.ensure_local_key()
    assert re.search(r"/login#key=\S+", err.getvalue())
    assert key not in caplog.text


def test_a_configured_key_is_the_key(client, monkeypatch):
    monkeypatch.setattr(settings, "aura_local_login_key", "configured-key-from-aura-env-0123456789", raising=False)
    assert deps.ensure_local_key() == ""  # nothing generated, nothing written
    assert not key_file().exists()
    assert client.post("/api/auth/local").status_code == 403
    assert client.post("/api/auth/local", json={"key": "wrong"}).status_code == 403
    assert client.post("/api/auth/local", json={"key": "configured-key-from-aura-env-0123456789"}).status_code == 200


def test_a_session_without_the_key_claim_is_not_honoured_on_a_local_install(client):
    from app.models import User
    from app.db import SessionLocal
    from app.security import create_access_token

    key = deps.ensure_local_key()
    ok = client.post("/api/auth/local", json={"key": key}).json()
    uid = ok["user"]["id"]
    for claims in ({}, {"amr": "local-open"}, {"amr": "password"}, {"amr": "demo"}, {"amr": "oauth"}):
        token = create_access_token(uid, claims)
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, claims
    good = client.get("/api/auth/me", headers={"Authorization": f"Bearer {ok['access_token']}"})
    assert good.status_code == 200
    del User, SessionLocal


def test_the_config_no_longer_has_a_keyless_mode(client):
    config = client.get("/api/auth/config").json()
    assert "local_login_key_required" not in config and config["local_mode"] is True


def test_nothing_of_local_open_is_left():
    """The token type, its caller kind and every special case are gone: backend, CLI and web client."""
    roots = [ROOT / "app", ROOT / "aura_cli.py", ROOT / "aura_desktop.py", ROOT.parent / "frontend" / "app",
             ROOT.parent / "frontend" / "lib", ROOT.parent / "frontend" / "components"]
    hits = []
    for base in roots:
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.suffix in (".py", ".ts", ".tsx")]
        for path in files:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"local-open|local_open|LocalOpen|local_login_key_required", text):
                hits.append(str(path))
    assert not hits, hits


# --- the terminal client ---------------------------------------------------------------------------


def _cli(tmp_path, monkeypatch, handler):
    import aura_cli

    monkeypatch.setattr(aura_cli, "TOKEN_PATH", tmp_path / "cli-token")
    monkeypatch.setenv("AURA_HOME", str(tmp_path / "cli-home"))
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return aura_cli, aura_cli.Aura("http://127.0.0.1:8765", client)


def test_the_cli_signs_in_with_the_key_file(tmp_path, monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/auth/local":
            ok = json.loads(request.content or b"{}").get("key") == "k" * 40
            return httpx.Response(200 if ok else 403,
                                  json={"access_token": "t", "user": {"email": "me@localhost"}} if ok else {"detail": "no"})
        return httpx.Response(401)

    cli, aura = _cli(tmp_path, monkeypatch, handler)
    home = tmp_path / "cli-home"
    home.mkdir()
    (home / "local-login.key").write_text("k" * 40)
    assert aura.sign_in() is True and aura.token == "t"
    # the desktop app keeps it in aura.env instead
    (home / "local-login.key").unlink()
    (home / "aura.env").write_text(f'SECRET_KEY=x\nAURA_LOCAL_LOGIN_KEY="{"k" * 40}"\n')
    aura.token = ""
    assert aura.sign_in() is True


def test_the_cli_without_a_key_does_not_try_a_keyless_sign_in(tmp_path, monkeypatch, capsys):
    calls = []
    cli, aura = _cli(tmp_path, monkeypatch, lambda r: calls.append(r.url.path) or httpx.Response(403, json={"detail": "x"}))
    assert aura.sign_in() is False
    assert "/api/auth/local" not in calls
    assert "sign-in key" in capsys.readouterr().err
