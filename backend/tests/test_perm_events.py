"""The permission event log: hash chain, signature, retries (spec 10 section 2.6)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import settings  # noqa: E402
from app.permissions import events  # noqa: E402
from tests.perm_helpers import make_session, make_user  # noqa: E402


@pytest.fixture
def env(tmp_path):
    engine, db = make_session(tmp_path)
    user = make_user(db)
    yield engine, db, user
    db.close()
    engine.dispose()


def fill(db, user, n=5):
    for i in range(n):
        events.append(db, user.id, "decision", actor="aura", tool=f"tool{i}", target=f"path:/x/{i}",
                      outcome="allow", reason_code="MODE_SKIP", run_key="r1", detail={"i": i})


def raw(engine, sql, **params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)


def test_chain_verifies(env):
    _, db, user = env
    assert events.verify(db, user.id) == {"ok": True, "checked": 0, "first_bad_seq": None}
    fill(db, user, 6)
    rows = events.listing(db, user.id)
    assert [r.seq for r in rows] == [6, 5, 4, 3, 2, 1]
    assert rows[-1].prev_hash == "" and all(rows[i].prev_hash == rows[i + 1].hash for i in range(5))
    assert all(len(r.hash) == 64 and len(r.mac) == 64 for r in rows)
    assert events.verify(db, user.id) == {"ok": True, "checked": 6, "first_bad_seq": None}
    assert [r.seq for r in events.listing(db, user.id, before_seq=3, limit=10)] == [2, 1]


def test_chains_are_per_user(env):
    _, db, user = env
    other = make_user(db, "other@example.com")
    fill(db, user, 3)
    fill(db, other, 2)
    assert [r.seq for r in events.listing(db, other.id)] == [2, 1]
    assert events.verify(db, user.id)["checked"] == 3 and events.verify(db, other.id)["checked"] == 2


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE permission_events SET outcome='deny' WHERE seq=3",
        "UPDATE permission_events SET target='path:/etc' WHERE seq=3",
        "UPDATE permission_events SET detail='{\"i\": 99}' WHERE seq=3",
        "UPDATE permission_events SET tool='other' WHERE seq=3",
        "UPDATE permission_events SET created_at='2001-01-01 00:00:00.000000' WHERE seq=3",
        "UPDATE permission_events SET user_id='u' WHERE seq=3 AND 0",  # no-op control, must stay valid
    ],
)
def test_edited_row_detected(env, sql):
    engine, db, user = env
    fill(db, user, 5)
    raw(engine, sql)
    db.expire_all()
    result = events.verify(db, user.id)
    if "AND 0" in sql:
        assert result["ok"]
    else:
        assert result == {"ok": False, "checked": 2, "first_bad_seq": 3}


def test_deleted_or_reordered_rows_detected(env):
    engine, db, user = env
    fill(db, user, 5)
    raw(engine, "DELETE FROM permission_events WHERE seq=3")
    db.expire_all()
    assert events.verify(db, user.id)["ok"] is False
    assert events.verify(db, user.id)["first_bad_seq"] == 3


def test_a_rewritten_hash_without_the_key_fails_the_signature(env):
    """Someone who edits a row and recomputes the whole chain still cannot sign it."""
    engine, db, user = env
    fill(db, user, 3)
    rows = {r.seq: r for r in events.listing(db, user.id)}
    row = rows[3]
    row.outcome = "deny"
    row.hash = events._digest(row.prev_hash, events._row_fields(row), row.created_at)  # consistent hash...
    db.commit()
    result = events.verify(db, user.id)
    assert result["ok"] is False and result["first_bad_seq"] == 3  # ...but the mac was made over the old one


def test_the_signing_key_comes_from_the_server_secret(env, monkeypatch):
    _, db, user = env
    fill(db, user, 2)
    monkeypatch.setattr(settings, "secret_key", "a-different-secret", raising=False)
    assert events.verify(db, user.id)["ok"] is False


def test_concurrent_append_retries(env):
    engine, db, user = env
    fill(db, user, 1)
    rival = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    original = db.begin_nested
    state = {"stolen": False}

    def contested():
        if not state["stolen"]:
            state["stolen"] = True
            events.append(rival, user.id, "mode_changed", actor="user")  # takes seq 2 first
        return original()

    db.begin_nested = contested  # type: ignore[method-assign]
    row = events.append(db, user.id, "rule_added", actor="user")
    del db.begin_nested
    rival.close()
    assert state["stolen"] and row.seq == 3
    assert [r.kind for r in reversed(events.listing(db, user.id))] == ["decision", "mode_changed", "rule_added"]
    assert events.verify(db, user.id)["ok"]


def test_gives_up_after_five_collisions(env):
    _, db, user = env
    fill(db, user, 1)
    from sqlalchemy.exc import IntegrityError

    class Boom:
        def __enter__(self):
            raise IntegrityError("x", {}, Exception("dup"))

        def __exit__(self, *a):
            return False

    db.begin_nested = lambda: Boom()  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        events.append(db, user.id, "rule_added")
    del db.begin_nested


def test_secrets_are_not_stored(env):
    _, db, user = env
    events.append(db, user.id, "decision", tool="run_command", target="command:curl -H 'Authorization: Bearer abcdefghijklmnop' x",
                  detail={"note": "password=hunter2 and token: abc123", "blob": "A" * 80, "ok": "fine"})
    row = events.listing(db, user.id)[0]
    flat = row.target + str(row.detail)
    assert "hunter2" not in flat and "abc123" not in flat and "A" * 40 not in flat and "abcdefghijklmnop" not in flat
    assert row.detail["ok"] == "fine"
    assert events.verify(db, user.id)["ok"]
