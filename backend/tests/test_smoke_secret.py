"""The launcher's self check (`aura_desktop.py --smoke-test`) calls its own server from inside the AURA
process, which the peer guard refuses by design. Only that mode registers an in-memory secret with the guard,
through `deps.register_smoke_secret`; a request carrying it counts as the user at the keyboard.

Run from backend/:  pytest -q -p no:cacheprovider tests/test_smoke_secret.py"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.config import settings
from app.db import engine
from app.device.runtime import reset_cache
from app.main import app
from app.permissions import access
from tests.schema import drop_test_schema, ensure_test_schema
from tests.test_sec_f3_peer import fake_psutil

KEY = "smoke-key-0123456789abcdef0123456789abcdef"
SECRET = "unit-test-smoke-secret"
BACKEND = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.real_peer

# one route behind GuardedUser (chat) and one behind the presence guard (strict)
ROUTES = [
    ("POST", "/api/chat/stream", {"message": "hi"}),
    ("POST", "/api/pending-actions/x", {"decision": "reject"}),
]


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
    monkeypatch.setattr(settings, "aura_home", str(tmp_path / "home"), raising=False)
    monkeypatch.setattr(settings, "aura_local_login_key", KEY, raising=False)
    monkeypatch.setattr(settings, "device_allowed_roots", "", raising=False)
    monkeypatch.setattr(deps, "_smoke_secret", None)  # restored afterwards
    # every request looks like it comes from the AURA process itself
    monkeypatch.setattr(deps, "_load_psutil", lambda: fake_psutil({50000: os.getpid()}))
    reset_cache()
    access.reset_state()
    yield
    access.reset_state()


@pytest.fixture
def owner(client):
    r = client.post("/api/auth/local", json={"key": KEY})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _refused(r) -> bool:
    return r.status_code == 403 and "started by AURA" in r.text


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_the_header_is_ignored_when_nothing_is_registered(client, owner, method, path, body):
    for value in (SECRET, "", "None", "null"):
        r = client.request(method, path, headers={**owner, deps.SMOKE_HEADER: value}, json=body)
        assert _refused(r), (path, value, r.status_code, r.text)


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_a_wrong_value_is_refused(client, owner, monkeypatch, method, path, body):
    deps.register_smoke_secret(SECRET)
    for value in ("wrong", SECRET + "x", SECRET[:-1], "", SECRET.upper()):
        r = client.request(method, path, headers={**owner, deps.SMOKE_HEADER: value}, json=body)
        assert _refused(r), (path, value, r.status_code, r.text)
    # and no header at all
    assert _refused(client.request(method, path, headers=owner, json=body))


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_the_right_value_passes(client, owner, method, path, body):
    deps.register_smoke_secret(SECRET)
    r = client.request(method, path, headers={**owner, deps.SMOKE_HEADER: SECRET}, json=body)
    assert not _refused(r) and r.status_code != 403, (path, r.status_code, r.text)


def test_the_secret_only_covers_the_guard_not_the_sign_in(client):
    deps.register_smoke_secret(SECRET)
    r = client.post("/api/chat/stream", headers={deps.SMOKE_HEADER: SECRET}, json={"message": "hi"})
    assert r.status_code == 401


def test_the_comparison_is_constant_time():
    src = (BACKEND / "app" / "deps.py").read_text(encoding="utf-8")
    body = src[src.index("def _carries_smoke_secret"):src.index("def _refuse_self")]
    assert "hmac.compare_digest" in body and "==" not in body


def test_the_only_setter_is_the_in_process_function():
    """Read the code: nothing but `register_smoke_secret` assigns the secret, the header and the secret
    are not reachable from a route, the settings or the environment, and only the launcher calls it."""
    app_dir = BACKEND / "app"
    writers, readers = [], []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "_smoke_secret" in text or "register_smoke_secret" in text or "SMOKE_HEADER" in text:
            readers.append(path.relative_to(BACKEND).as_posix())
        if path.name != "deps.py" and re.search(r"smoke_secret|SMOKE_HEADER|Aura-Smoke", text, re.I):
            writers.append(path.relative_to(BACKEND).as_posix())
    assert readers == ["app/deps.py"] and writers == []

    deps_src = (app_dir / "deps.py").read_text(encoding="utf-8")
    assigns = re.findall(r"^\s*_smoke_secret\s*(?::[^=\n]+)?=(?!=).*$", deps_src, re.M)
    assert len(assigns) == 2, assigns  # the module default (None) and the one inside the function
    fn = deps_src[deps_src.index("def register_smoke_secret"):deps_src.index("def _carries_smoke_secret")]
    assert "global _smoke_secret" in fn and "_smoke_secret =" in fn
    # no environment variable, settings field or route feeds it
    smoke_lines = [ln for ln in deps_src.splitlines() if "smoke" in ln.lower() and not ln.lstrip().startswith("#")]
    assert not any("environ" in ln or "getenv" in ln or "settings." in ln for ln in smoke_lines)
    assert "register_smoke_secret" not in (app_dir / "config.py").read_text(encoding="utf-8")
    assert not hasattr(settings, "smoke_secret") and not hasattr(settings, "aura_smoke_secret")
    # no route is declared in the module that holds the function
    assert not re.search(r"@(app|router)\.(get|post|put|patch|delete)", deps_src)

    launcher = (BACKEND / "aura_desktop.py").read_text(encoding="utf-8")
    calls = [ln for ln in launcher.splitlines() if "register_smoke_secret(" in ln]
    assert len(calls) == 1
    main_src = launcher[launcher.index("def main("):]
    block = main_src[main_src.index("register_smoke_secret(") - 400:main_src.index("register_smoke_secret(")]
    assert "if smoke:" in block  # registered only in --smoke-test mode
    secret_use = [ln for ln in launcher.splitlines() if "smoke_secret" in ln and "log" in ln.lower()]
    assert not secret_use
    assert "os.environ" not in "\n".join(ln for ln in launcher.splitlines() if "smoke_secret" in ln)


def test_a_normal_start_registers_nothing():
    assert deps._smoke_secret is None
