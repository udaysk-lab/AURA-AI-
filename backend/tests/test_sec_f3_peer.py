"""F3: the "not a program AURA started" guard covers every owner route, and an unidentifiable peer is refused
where permissions change. Escaping the guard (setsid, env -i) leaves a program with no token (F2 + F5)."""

from __future__ import annotations

import os
import socket
import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.config import settings
from app.db import SessionLocal, engine
from app.device import shell
from app.device.runtime import reset_cache
from app.main import app
from app.permissions import access
from tests.schema import drop_test_schema, ensure_test_schema

KEY = "f3-key-0123456789abcdef0123456789abcdef"

pytestmark = pytest.mark.real_peer


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
    reset_cache()
    access.reset_state()
    yield
    access.reset_state()


@pytest.fixture
def owner(client):
    r = client.post("/api/auth/local", json={"key": KEY})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def fake_psutil(table, *, environ=None, parents=(), name="python"):
    class Proc:
        def __init__(self, pid):
            self.pid = pid

        def environ(self):
            return dict(environ or {})

        def parents(self):
            return [types.SimpleNamespace(pid=p, environ=lambda: {}) for p in parents]

        def name(self):
            return name

    def conns(kind):
        return [types.SimpleNamespace(laddr=types.SimpleNamespace(port=port), pid=pid) for port, pid in table.items()]

    return types.SimpleNamespace(net_connections=conns, Process=Proc)


# every route that acts as the owner on a local install (the review's list)
OWNER_ROUTES = [
    ("POST", "/api/chat", {"message": "hi"}),
    ("POST", "/api/chat/stream", {"message": "hi"}),
    ("POST", "/api/chat/continue", {"work_item_id": "x"}),
    ("POST", "/api/chat/stop", {"work_item_id": "x"}),
    ("POST", "/api/device/presence", {}),
    ("POST", "/api/channels/connect", {"kind": "telegram", "identifier": "x"}),
    ("POST", "/api/channels/telegram/rotate", None),
    ("PUT", "/api/channels/telegram/owner", {"owner_sender": "x", "bridge_owner_only": False}),
    ("POST", "/api/schedules/nope/run", None),
    ("POST", "/api/automations/nope/run", None),
    ("POST", "/api/automations/tick", None),
]


@pytest.mark.parametrize("label,table,kw", [
    ("the server itself", lambda: {50000: os.getpid()}, {}),
    ("carries the marker", lambda: {50000: 31337}, {"environ": {"AURA_SPAWNED_BY": deps._instance_id()}}),
    ("descends from the server", lambda: {50000: 31337}, {"parents": (4000, os.getpid())}),
])
@pytest.mark.parametrize("method,path,body", OWNER_ROUTES)
def test_a_program_aura_started_is_refused_on_every_owner_route(client, owner, monkeypatch, label, table, kw,
                                                                method, path, body):
    monkeypatch.setattr(deps, "_load_psutil", lambda: fake_psutil(table(), **kw))
    r = client.request(method, path, headers=owner, json=body)
    assert r.status_code == 403, (label, path, r.status_code, r.text)
    assert "started by AURA" in r.json()["detail"]


@pytest.mark.parametrize("method,path,body", OWNER_ROUTES)
def test_an_ordinary_program_reaches_them(client, owner, monkeypatch, method, path, body):
    monkeypatch.setattr(deps, "_load_psutil", lambda: fake_psutil({50000: 31337}, parents=(1,)))
    r = client.request(method, path, headers=owner, json=body)
    assert r.status_code != 403 or "started by AURA" not in r.text, (path, r.text)


def test_permission_changing_routes_refuse_an_unidentifiable_peer(client, owner, monkeypatch):
    monkeypatch.setattr(deps, "_load_psutil", lambda: None)
    monkeypatch.setattr(deps, "_run_tool", lambda argv: (_ for _ in ()).throw(FileNotFoundError(argv[0])))
    for method, path, body in [("PUT", "/api/device/screen-control", {"enabled": True}),
                               ("PUT", "/api/channels/telegram/owner", {"owner_sender": "x", "bridge_owner_only": False}),
                               ("POST", "/api/pending-actions/x", {"decision": "reject"})]:
        r = client.request(method, path, headers=owner, json=body)
        assert r.status_code == 403 and "could not tell" in r.json()["detail"], path
    # ... while chat, which changes no permission, still works when the lookup cannot be done
    assert client.post("/api/chat", headers=owner, json={"message": "hi"}).status_code != 403


def _server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    return srv


def _req(addr):
    return types.SimpleNamespace(client=types.SimpleNamespace(host=addr[0], port=addr[1]))


@pytest.mark.skipif(sys.platform == "win32", reason="lsof / ss are POSIX tools; netstat is parsed below")
def test_platform_tools_find_the_peer_without_psutil(monkeypatch):
    """No psutil: lsof (macOS) / ss (Linux) still identify a real loopback client, so the real browser passes."""
    import shutil
    import subprocess

    if not (shutil.which("lsof") or shutil.which("ss")):
        pytest.skip("neither lsof nor ss is installed")
    monkeypatch.setattr(deps, "_load_psutil", lambda: None)
    srv = _server()
    port = srv.getsockname()[1]
    client_code = f"import socket,time;s=socket.create_connection(('127.0.0.1',{port}));time.sleep(5)"
    # launched by a helper that exits at once, so it is not one of this process's descendants (a browser)
    launcher = ("import subprocess,sys;subprocess.Popen([sys.executable,'-c',sys.argv[1]],start_new_session=True,"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)")
    env = {k: v for k, v in os.environ.items() if k != "AURA_SPAWNED_BY"}
    subprocess.run([sys.executable, "-c", launcher, client_code], env=env, check=True)
    child = None
    try:
        conn, addr = srv.accept()
        time.sleep(0.3)
        assert deps._find_peer_pid(addr[1])
        assert deps.classify_peer(_req(addr)) == "other"          # an ordinary program: let through
        assert deps.classify_peer(_req(("127.0.0.1", 1))) == "unknown"   # nothing owns that port
        conn.close()
    finally:
        srv.close()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_a_shell_tool_child_is_caught_without_psutil(monkeypatch, tmp_path):
    """poc3: the direct child carries the marker; with psutil missing the guard still sees it."""
    import shutil

    if not (shutil.which("lsof") or shutil.which("ss")):
        pytest.skip("neither lsof nor ss is installed")
    monkeypatch.setattr(deps, "_load_psutil", lambda: None)
    from tests.sec_world import build

    w = build(tmp_path, monkeypatch)
    srv = _server()
    port = srv.getsockname()[1]
    cmd = f"{sys.executable} -c \"import socket,time;s=socket.create_connection(('127.0.0.1',{port}));time.sleep(3)\" >/dev/null 2>&1 &"
    with access.use(access.snapshot(w.db, w.user)):
        shell.run(cmd, w.work, 5)
    conn, addr = srv.accept()
    time.sleep(0.3)
    assert deps.classify_peer(_req(addr)) == "aura"
    conn.close()
    srv.close()
    w.db.close()


def test_netstat_output_is_read():
    out = ("  Proto  Local Address          Foreign Address        State           PID\n"
           "  TCP    127.0.0.1:50123        127.0.0.1:8765         ESTABLISHED     4242\n"
           "  TCP    127.0.0.1:8765         127.0.0.1:50123        ESTABLISHED     999\n")
    orig = deps._run_tool
    deps._run_tool = lambda argv: out
    try:
        assert deps._pid_from_netstat(50123) == 4242
        assert deps._pid_from_netstat(1) is None
    finally:
        deps._run_tool = orig


def test_an_escaped_process_has_no_token(tmp_path, monkeypatch):
    """poc3: `setsid env -i` drops the marker and the parent; with F2 and F5 it also has nothing to sign in with."""
    from tests.sec_world import build

    w = build(tmp_path, monkeypatch)
    monkeypatch.setenv("AURA_LOCAL_LOGIN_KEY", KEY)
    monkeypatch.setenv("SECRET_KEY", "jwt-secret-from-aura-env")
    py = sys.executable
    script = tmp_path / "dump.py"
    script.write_text("import os;print(sorted(k for k in os.environ if 'KEY' in k or k.startswith('AURA')))")
    with access.use(access.snapshot(w.db, w.user)):
        r = shell.run(f"'{py}' '{script}'", w.work, 20)
    assert "AURA_LOCAL_LOGIN_KEY" not in r["output"] and "SECRET_KEY" not in r["output"]
    # and no key means no session
    key_file = os.path.join(w.aura_home, "local-login.key")
    assert not os.path.exists(key_file) or KEY not in open(key_file).read()
    w.db.close()
