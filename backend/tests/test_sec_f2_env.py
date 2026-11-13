"""F2: programs AURA starts do not inherit AURA's own configuration."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.device import childenv, shell
from app.device.runtime import capabilities
from tests.sec_world import build

SECRETS = {
    "SECRET_KEY": "jwt-signing-secret-from-aura.env",
    "TOKEN_ENCRYPTION_KEY": "fernet-key-from-aura.env",
    "AURA_LOCAL_LOGIN_KEY": "local-login-key-from-aura.env",
    "ANTHROPIC_API_KEY": "sk-ant-live",
    "anthropic_model": "lower-case-still-config",
    "AURA_WEB_DIR": "/somewhere",
    "SMTP_PASSWORD": "smtp-pass",
    "DATABASE_URL": "postgresql://u:pw@db/aura",
    "POSTGRES_URL": "postgresql://u:pw@db/pooled",
}


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    # a name that is not a settings field at all, but is written in AURA's own settings file
    (Path(world.aura_home) / "aura.env").write_text("MY_ODD_SETTING_IN_AURA_ENV=hunter2\nSECRET_KEY=x\n")
    monkeypatch.setattr(settings, "aura_config_file", str(Path(world.aura_home) / "aura.env"), raising=False)
    monkeypatch.setenv("MY_ODD_SETTING_IN_AURA_ENV", "hunter2")
    monkeypatch.setenv("MY_OWN_TOOL_VAR", "keep-me")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    yield world
    world.db.close()
    world.engine.dispose()


def test_child_env_strips_every_aura_setting_and_keeps_the_users_own(w):
    env = childenv.child_env()
    upper = {k.upper() for k in env}
    for name in list(SECRETS) + ["MY_ODD_SETTING_IN_AURA_ENV"]:
        assert name.upper() not in upper, name
    assert not any(k.upper().startswith("AURA_") for k in env)
    assert env["MY_OWN_TOOL_VAR"] == "keep-me" and env["LANG"] == "en_US.UTF-8"
    assert env.get("PATH") == os.environ.get("PATH")
    # what the caller adds afterwards is kept (the markers the process guard looks for)
    assert childenv.child_env({"AURA_SPAWNED_BY": "x"})["AURA_SPAWNED_BY"] == "x"


def test_every_settings_field_is_stripped():
    names = childenv.aura_names()
    for field in Settings.model_fields:
        assert field.upper() in names


def test_every_variable_the_app_reads_is_stripped():
    """A new os.environ read added anywhere in app/ must be in the strip list (or be an OS variable)."""
    os_level = {"SHELL", "DISPLAY", "WAYLAND_DISPLAY", "USER", "USERNAME", "NO_COLOR", "VERCEL_ENV", "HOME",
                "USERPROFILE", "PATH", "TERM"}
    root = Path(__file__).resolve().parents[1] / "app"
    names = childenv.aura_names()
    found = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(r"os\.environ\.get\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']", text))
        found |= set(re.findall(r"os\.environ\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']", text))
        found |= set(re.findall(r"os\.getenv\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']", text))
    missing = {n for n in found if n.upper() not in names and not n.upper().startswith("AURA_") and n not in os_level}
    assert not missing, missing


def _script(tmp_path):
    p = tmp_path / "dump_env.py"
    p.write_text("import json, os, sys\nprint(json.dumps(dict(os.environ)))\n", encoding="utf-8")
    return str(p)


def _invoke(script: str) -> str:
    """A command line that runs the script under whichever shell AURA picked on this machine."""
    kind = capabilities()["shell"]["kind"]
    exe = sys.executable
    if kind in ("pwsh", "powershell"):
        return f"& '{exe}' '{script}'"
    if kind == "cmd":
        return f'"{exe}" "{script}"'
    return f"'{exe}' '{script}'"


def _decode(output: str) -> dict:
    line = next(l for l in output.splitlines() if l.startswith("{"))
    return json.loads(line)


def test_a_foreground_command_cannot_see_aura_settings(w, tmp_path):
    from app.permissions import access

    with access.use(access.snapshot(w.db, w.user)):
        result = shell.run(_invoke(_script(tmp_path)), w.work, 30)
    seen = _decode(result["output"])
    assert not {k.upper() for k in seen} & {n.upper() for n in SECRETS}
    assert "MY_ODD_SETTING_IN_AURA_ENV" not in {k.upper() for k in seen}
    assert seen["MY_OWN_TOOL_VAR"] == "keep-me" and "PATH" in {k.upper() for k in seen}
    assert seen["AURA_SHELL"] == "exec" and "AURA_SPAWNED_BY" in seen  # the guard's own markers
    assert "super" not in json.dumps(seen) and "local-login-key" not in json.dumps(seen)


def test_a_background_command_cannot_see_aura_settings(w, tmp_path):
    import time

    from app.permissions import access

    with access.use(access.snapshot(w.db, w.user)):
        started = shell.run_in_background(_invoke(_script(tmp_path)), w.work)
        out = ""
        for _ in range(100):
            out = shell.poll(started["session_id"], 50).get("output", "")
            if "{" in out and "}" in out:
                break
            time.sleep(0.1)
    seen = _decode(out)
    assert not {k.upper() for k in seen} & {n.upper() for n in SECRETS}
    assert seen["MY_OWN_TOOL_VAR"] == "keep-me"


@pytest.mark.parametrize("kind", ["posix", "pwsh", "cmd"])
def test_all_three_shells_start_with_the_same_environment(w, kind, monkeypatch):
    """Whatever shell wraps the command, the environment passed to it is the stripped one."""
    captured = {}

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kw):
        captured["env"] = kw["env"]
        return Done()

    monkeypatch.setattr(shell.subprocess, "run", fake_run)
    shells = {"posix": ("bash", "/bin/bash"), "pwsh": ("pwsh", "pwsh"), "cmd": ("cmd", "cmd.exe")}
    monkeypatch.setattr(shell, "capabilities", lambda: {"shell": {"kind": shells[kind][0], "path": shells[kind][1]}})
    from app.permissions import access

    with access.use(access.snapshot(w.db, w.user)):
        shell.run("echo hi", w.work, 5)
    env = captured["env"]
    assert not {k.upper() for k in env} & {n.upper() for n in SECRETS}
    assert env["MY_OWN_TOOL_VAR"] == "keep-me"


def test_the_desktop_launcher_does_not_export_its_settings_file(tmp_path):
    """aura.env reaches the settings loader directly, never the process environment."""
    from unittest import mock

    import aura_desktop
    from app import envfile

    home = tmp_path / "desk"
    config = aura_desktop.ensure_home(home)
    if os.name != "nt":
        assert (config.stat().st_mode & 0o777) == 0o600
    values = envfile.read_values(config)
    generated = ("SECRET_KEY", "TOKEN_ENCRYPTION_KEY", "AURA_LOCAL_LOGIN_KEY")
    assert all(values[n] for n in generated)
    with mock.patch.dict(os.environ, clear=False):
        for name in (*generated, "AURA_CONFIG_FILE", "DATABASE_URL", "ANTHROPIC_API_KEY"):
            os.environ.pop(name, None)
        envfile.write_values({"ANTHROPIC_API_KEY": "sk-from-the-file", "DATABASE_URL": "sqlite:///./from-file.db"}, config)
        aura_desktop.apply_environment(home, config, 8765, "127.0.0.1", None)
        for name in (*generated, "ANTHROPIC_API_KEY"):
            assert name not in os.environ, name
        assert "DATABASE_URL" not in os.environ  # the file's value wins over the launcher's default
        loaded = Settings()
        assert loaded.database_url == "sqlite:///./from-file.db"
        assert loaded.secret_key == values["SECRET_KEY"]
        assert loaded.aura_local_login_key == values["AURA_LOCAL_LOGIN_KEY"]
        assert loaded.anthropic_api_key == "sk-from-the-file"
        # and a command started now sees none of them
        env = childenv.child_env()
        assert not {k.upper() for k in env} & set(generated)
