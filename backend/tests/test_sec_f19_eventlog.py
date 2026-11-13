"""F19: the permission log is signed with a key of its own on a local install, a quiet truncation is
reported, and a local install does not run on the development secret."""

from __future__ import annotations

import os
import stat
import sys

import pytest
from sqlalchemy import text

from app import deps
from app.config import settings
from app.permissions import events
from tests.perm_helpers import make_session, make_user


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "aura-home"
    monkeypatch.setattr(settings, "aura_home", str(home), raising=False)
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "secret_key", "s" * 40, raising=False)
    events._key_cache.clear()
    engine, db = make_session(tmp_path)
    user = make_user(db)
    yield engine, db, user, home
    db.close()
    engine.dispose()
    events._key_cache.clear()


def fill(db, user, n=5):
    for i in range(n):
        events.append(db, user.id, "decision", actor="aura", tool=f"t{i}", outcome="allow", detail={"i": i})


def test_a_local_install_signs_with_its_own_key(env, monkeypatch):
    engine, db, user, home = env
    fill(db, user, 3)
    keyfile = home / events.LOG_KEY_FILE
    assert keyfile.is_file() and len(keyfile.read_text().strip()) == 64
    if os.name != "nt":
        assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600
    assert events.verify(db, user.id)["ok"]
    # the server secret no longer matters to the log ...
    monkeypatch.setattr(settings, "secret_key", "a-completely-different-secret-value-123", raising=False)
    assert events.verify(db, user.id)["ok"]
    # ... and a different log key does
    events._key_cache.clear()
    keyfile.write_text("ab" * 32)
    assert events.verify(db, user.id)["ok"] is False


def test_the_key_is_not_derived_from_the_secret_a_forger_could_know(env):
    engine, db, user, home = env
    fill(db, user, 2)
    forged = events._hkdf(settings.secret_key.encode(), b"aura-permission-log")
    assert events._mac_key() != forged


def test_a_log_key_that_cannot_be_kept_refuses_to_record(env, monkeypatch):
    engine, db, user, home = env
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    with pytest.raises(RuntimeError):
        events.append(db, user.id, "decision", actor="aura")


def test_cutting_entries_off_the_end_is_reported(env):
    engine, db, user, home = env
    fill(db, user, 5)
    assert events.verify(db, user.id)["ok"]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM permission_events WHERE seq > 3"))
    db.expire_all()
    result = events.verify(db, user.id)
    assert result["ok"] is False and result["reason"] == "entries_missing" and result["first_bad_seq"] == 4


def test_the_last_entry_replaced_by_another_valid_one_is_reported(env):
    engine, db, user, home = env
    fill(db, user, 4)
    # an attacker with the key re-signs a different fourth entry
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM permission_events WHERE seq = 4"))
    db.expire_all()
    fill(db, user, 1)
    heads = events._read_heads()
    assert heads[user.id]["seq"] == 4  # the honest append refreshed the head, so this is the control
    assert events.verify(db, user.id)["ok"]
    # now put back the old head: the table says one thing, the second place another
    last = events.listing(db, user.id, limit=1)[0]
    events._write_head(user.id, 4, "0" * 64)
    result = events.verify(db, user.id)
    assert result["ok"] is False and result["reason"] == "head_mismatch" and last.seq == 4


def test_a_tampered_head_file_is_reported(env):
    engine, db, user, home = env
    fill(db, user, 2)
    path = home / events.HEADS_FILE
    path.write_text(path.read_text().replace('"seq": 2', '"seq": 1'))
    assert events.verify(db, user.id)["reason"] == "head_unreadable"


def test_a_missing_head_file_is_not_a_mismatch(env):
    engine, db, user, home = env
    fill(db, user, 2)
    (home / events.HEADS_FILE).unlink()
    assert events.verify(db, user.id) == {"ok": True, "checked": 2, "first_bad_seq": None}


def test_a_local_install_does_not_keep_the_development_secret(tmp_path, monkeypatch):
    home = tmp_path / "h"
    monkeypatch.setattr(settings, "aura_home", str(home), raising=False)
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "secret_key", deps.DEV_SECRET, raising=False)
    assert deps.ensure_local_secret() is True
    first = settings.secret_key
    assert first != deps.DEV_SECRET and len(first) >= 48
    assert (home / deps.SECRET_KEY_FILE).read_text().strip() == first
    monkeypatch.setattr(settings, "secret_key", deps.DEV_SECRET, raising=False)
    deps.ensure_local_secret()
    assert settings.secret_key == first, "the same secret after a restart, so sessions survive"
    # a configured secret, and a hosted install, are left alone
    monkeypatch.setattr(settings, "secret_key", "configured-" + "x" * 40, raising=False)
    assert deps.ensure_local_secret() is False
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    monkeypatch.setattr(settings, "secret_key", deps.DEV_SECRET, raising=False)
    assert deps.ensure_local_secret() is False and settings.secret_key == deps.DEV_SECRET
