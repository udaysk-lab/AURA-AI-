"""Shared set-up for the security regression tests (review findings F1 to F7, F12).

`build()` makes a throw-away home folder holding a connected `work` folder and a source-style
project folder (`proj`, the server's working directory) with a `.env` full of secrets, then
points HOME, the settings and the working directory at them.
"""

from __future__ import annotations

import os
import types

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

from app import plugins  # noqa: E402
from app.config import settings  # noqa: E402
from app.device.runtime import reset_cache  # noqa: E402
from app.permissions import access, gate  # noqa: E402
from tests.perm_helpers import connect, make_session, make_user  # noqa: E402

SECRET = "SECRET_KEY=super-secret-jwt-key-123"


def build(tmp_path, monkeypatch, *, mode="skip", connect_home=True, posix=True):
    home = tmp_path / "home"
    work = home / "work"
    proj = home / "code" / "aura" / "backend"
    work.mkdir(parents=True)
    proj.mkdir(parents=True)
    (proj / ".env").write_text(SECRET + "\n")
    (proj / "aura.db").write_text("not really a database")
    aura_home = home / ".aura"
    aura_home.mkdir()
    (aura_home / "aura.env").write_text("AURA_LOCAL_LOGIN_KEY=sk-local-key-999\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "aura_home", str(aura_home), raising=False)
    monkeypatch.setattr(settings, "device_allowed_roots", "", raising=False)
    monkeypatch.setattr(settings, "device_command_denylist", "", raising=False)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{proj / 'aura.db'}", raising=False)
    monkeypatch.chdir(proj)
    reset_cache()
    access.reset_state()
    if posix:
        monkeypatch.setattr(gate, "_dialect", lambda p: "posix")
    import app.agent.device_tools  # noqa: F401  (registers the tools)

    engine, db = make_session(tmp_path)
    user = make_user(db, mode=mode, migrated=True)
    plugins.install(db, user.id, "device-control")
    connect(db, user, str(home if connect_home else work))
    w = types.SimpleNamespace(db=db, user=user, home=str(home), work=str(work), proj=str(proj),
                              aura_home=str(aura_home), engine=engine)
    return w


def run(w, tool, args, trigger="chat"):
    """decide() then execute, the way the agent loop does it. Returns (decision, result or None)."""
    from app.agent.coordinator import execute_tool

    d = gate.decide(w.db, w.user, tool, args, gate.build_context(w.db, w.user, trigger))
    if d.kind != "allow":
        return d, None
    return d, execute_tool(w.db, w.user, tool, args, decision=d)
