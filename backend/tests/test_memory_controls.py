"""Memory (facts) after the split: kinds, compaction that never touches the new stores, the edit path."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.models import Memory, Preference, WorkItem  # noqa: E402
from app.services import memory as mem  # noqa: E402
from app.services import preferences as prefs  # noqa: E402
from tests.work_helpers import FakeProvider, finished_item, make_session, make_user  # noqa: E402


@pytest.fixture
def env(tmp_path):
    engine, db = make_session(tmp_path)
    user = make_user(db)
    yield db, user
    db.close()
    engine.dispose()


def test_memory_kinds_narrowed(env):
    db, user = env
    assert mem.KINDS == ("fact", "contact", "project", "decision")
    for kind, text in (("fact", "Priya leads the Series A round"), ("contact", "Sam is the designer"),
                       ("project", "Series A closes in Q3"), ("decision", "Chose FastAPI for the backend")):
        db.add(Memory(user_id=user.id, kind=kind, content=text, source="chat"))
    for kind in mem.LEGACY_KINDS:  # rows 1.0 wrote, left in place after the copy to preferences
        db.add(Memory(user_id=user.id, kind=kind, content=f"Series A {kind} legacy row", source="chat"))
    db.commit()
    hits = mem.search(db, user.id, "Series A", limit=10)
    assert {h.memory.kind for h in hits} == {"fact", "project"}
    assert not [h for h in hits if "legacy" in h.memory.content]
    block, ids = mem.build_context_block(db, user.id, "Series A")
    assert "legacy" not in block and len(ids) == 2


def test_remember_files_legacy_kinds_as_facts_for_now(env):
    db, user = env
    m = mem.remember(db, user.id, "Likes bullet summaries", kind="preference")
    assert m.kind == "fact"
    assert [h.memory.id for h in mem.search(db, user.id, "bullet summaries")] == [m.id]
    assert mem.remember(db, user.id, "Priya", kind="contact").kind == "contact"


def test_compaction_ignores_manual_and_new_stores(env):
    db, user = env
    dup = "Always books flights with an aisle seat"
    for text in (dup, dup, dup + " please"):
        db.add(Memory(user_id=user.id, kind="fact", content=text, source="chat"))
    manual = [Memory(user_id=user.id, kind="fact", content="Pinned by hand " + str(i), source="manual") for i in range(3)]
    manual += [Memory(user_id=user.id, kind="fact", content="Pinned by hand 0", source="manual")]  # an exact twin
    legacy = [Memory(user_id=user.id, kind="preference", content="Legacy twin", source="chat") for _ in range(2)]
    unused = Memory(user_id=user.id, kind="decision", content="Decayed soon", source="chat", confidence=0.26)
    db.add_all([*manual, *legacy, unused])
    pref = prefs.add(db, user, "Always book aisle seats", "other", "stated", "settings").preference
    item = finished_item(db, user, title="Book the flight", task_type="scheduling")
    db.commit()

    result = mem.compact(db, user.id)
    assert result["merged"] == 2, "only the three chat duplicates were merged"
    assert result == {"before": 4, "after": 1, "merged": 2, "promoted": 0, "dropped": 1}  # 3 chat facts + the decision
    db.expire_all()
    rows = db.scalars(select(Memory).where(Memory.user_id == user.id)).all()
    assert sum(1 for r in rows if "aisle" in r.content) == 1
    assert len([r for r in rows if r.source == "manual"]) == 4, "manual rows are neither merged, promoted nor decayed"
    assert len([r for r in rows if r.kind == "preference"]) == 2, "legacy kinds are left alone"
    assert unused.id not in {r.id for r in rows}
    assert db.get(Preference, pref.id) is not None and db.get(WorkItem, item.id) is not None
    again = mem.compact(db, user.id, dry_run=True)
    assert again["merged"] == 0


def test_compaction_counts_only_what_it_reads(env):
    db, user = env
    db.add(Memory(user_id=user.id, kind="fact", content="one", source="chat", use_count=1))
    db.add(Memory(user_id=user.id, kind="style", content="two", source="chat"))
    db.add(Memory(user_id=user.id, kind="fact", content="three", source="manual"))
    db.commit()
    assert mem.compact(db, user.id) == {"before": 1, "after": 1, "merged": 0, "promoted": 0, "dropped": 0}


def test_patch_memory_content_reembeds_and_marks_manual(env, monkeypatch):
    db, user = env
    m = Memory(user_id=user.id, kind="fact", content="Priya is in London", source="chat", confidence=0.7,
               embedding=[0.1], pinned=False)
    db.add(m)
    db.commit()
    monkeypatch.setattr(mem, "get_provider", lambda: FakeProvider(vectors=lambda t: [float(len(t))]))
    out = mem.edit(db, m, content="  Priya is in Paris now  ", kind="contact", pinned=True)
    assert (out.content, out.kind, out.pinned, out.source) == ("Priya is in Paris now", "contact", True, "manual")
    assert out.embedding == [float(len("Priya is in Paris now"))]
    # kind and pin alone do not re-embed
    before = list(out.embedding)
    mem.edit(db, out, pinned=False)
    assert out.embedding == before and out.pinned is False and out.source == "manual"
    for bad in ({"content": "  "}, {"content": "x" * 2001}, {"kind": "preference"}, {"kind": "style"}):
        with pytest.raises(ValueError):
            mem.edit(db, out, **bad)
    assert mem.compact(db, user.id)["before"] == 0, "and compaction never sees it again"


def test_public_scoring_helpers_are_exported():
    assert mem.tokens("The quick brown fox") == {"quick", "brown", "fox"}
    assert mem.cosine([1.0, 0.0], [1.0, 0.0]) == 1.0 and mem.cosine([], [1.0]) == 0.0
    assert mem.lexical("quick fox", "quick dog") == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# The controls over HTTP (spec 20 sections 11 and 13)
# ---------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from tests.schema import ensure_test_schema  # noqa: E402

EMAILS = ("controls-a@example.com", "controls-b@example.com")


@pytest.fixture(scope="module")
def client():
    ensure_test_schema(engine)
    with TestClient(app) as c:
        yield c
    db = SessionLocal()
    try:
        for row in db.query(models.User).filter(models.User.email.in_(EMAILS)).all():
            db.delete(row)
        db.commit()
    finally:
        db.close()


def _sign_in(client, email):
    resp = client.post("/api/auth/demo", json={"email": email, "name": "Controls"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="module")
def ha(client):
    return _sign_in(client, EMAILS[0])


@pytest.fixture(scope="module")
def hb(client):
    return _sign_in(client, EMAILS[1])


def _uid(client, h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def _seed_item(client, h, title="Send Priya the Q3 numbers", status="done", **kw):
    db = SessionLocal()
    try:
        item = finished_item(db, db.get(models.User, _uid(client, h)), title=title, status=status,
                             task_type="email", **kw)
        item_id = item.id
    finally:
        db.close()
    return item_id


def test_work_item_crud_and_owner_isolation(client, ha, hb):
    a = _seed_item(client, ha, title="Send Priya the Q3 numbers", instruction="email priya the q3 numbers")
    _seed_item(client, ha, title="Plan the offsite", instruction="book a venue", status="failed")
    b = _seed_item(client, hb, title="Someone else's task")

    rows = client.get("/api/work-items", headers=ha).json()
    assert a in [r["id"] for r in rows] and b not in [r["id"] for r in rows]
    first = next(r for r in rows if r["id"] == a)
    assert set(first) == {"id", "title", "task_type", "status", "trigger", "conversation_id", "outcome",
                          "verification", "steps_total", "cost_usd", "recallable", "created_at", "finished_at"}
    assert first["verification"]["status"] == "none"
    # filters: status (one or a list), type, text, limit, before
    assert [r["title"] for r in client.get("/api/work-items?status=failed", headers=ha).json()] == ["Plan the offsite"]
    assert {r["status"] for r in client.get("/api/work-items?status=done,failed", headers=ha).json()} == {"done", "failed"}
    assert all(r["task_type"] == "email" for r in client.get("/api/work-items?task_type=email", headers=ha).json())
    assert [r["id"] for r in client.get("/api/work-items?q=Q3%20NUMBERS", headers=ha).json()] == [a]
    assert [r["id"] for r in client.get("/api/work-items?q=venue", headers=ha).json()][0:1] != [a]  # instruction text
    assert client.get("/api/work-items?q=%25", headers=ha).json() == []  # a wildcard is just text
    assert len(client.get("/api/work-items?limit=1", headers=ha).json()) == 1
    assert client.get("/api/work-items?limit=0", headers=ha).status_code == 422
    future = (models.utcnow() + timedelta(days=1)).isoformat()
    past = (models.utcnow() - timedelta(days=1)).isoformat()
    assert client.get("/api/work-items", params={"before": future}, headers=ha).json()
    assert client.get("/api/work-items", params={"before": past}, headers=ha).json() == []
    assert client.get("/api/work-items?before=yesterday", headers=ha).status_code == 422

    detail = client.get(f"/api/work-items/{a}", headers=ha).json()
    assert detail["instruction"] == "email priya the q3 numbers" and detail["steps"] == []
    assert detail["recall_used"] == {"past_work": [], "preferences": []} and detail["pending_action_ids"] == []
    assert detail["user_feedback"] == {} and isinstance(detail["verification"], dict)

    patched = client.patch(f"/api/work-items/{a}", headers=ha, json={"title": "Q3 numbers to Priya",
                                                                     "task_type": "email", "recallable": False,
                                                                     "summary": "Drafted and sent."})
    assert patched.status_code == 200
    body = patched.json()
    assert (body["title"], body["recallable"], body["summary"]) == ("Q3 numbers to Priya", False, "Drafted and sent.")
    assert client.patch(f"/api/work-items/{a}", headers=ha, json={"title": "  "}).status_code == 422
    assert client.patch(f"/api/work-items/{a}", headers=ha, json={"title": ""}).status_code == 422

    fb = client.post(f"/api/work-items/{a}/feedback", headers=ha, json={"rating": "down", "note": "too long"})
    assert fb.status_code == 200 and fb.json()["user_feedback"]["rating"] == "down"
    assert fb.json()["user_feedback"]["note"] == "too long"
    pref = [p for p in client.get("/api/preferences", headers=ha).json() if p["origin"] == "feedback"]
    assert pref and pref[0]["source"] == "correction" and pref[0]["task_type"] == "email"
    assert client.post(f"/api/work-items/{a}/feedback", headers=ha, json={"rating": "meh"}).status_code == 422
    assert client.post(f"/api/work-items/{a}/feedback", headers=ha,
                       json={"rating": "up", "note": "x" * 501}).status_code == 422

    # the other account sees and changes none of it
    for call in (lambda: client.get(f"/api/work-items/{a}", headers=hb),
                 lambda: client.patch(f"/api/work-items/{a}", headers=hb, json={"title": "mine now"}),
                 lambda: client.post(f"/api/work-items/{a}/feedback", headers=hb, json={"rating": "up"}),
                 lambda: client.delete(f"/api/work-items/{a}", headers=hb)):
        assert call().status_code == 404
    assert client.get("/api/work-items/nope", headers=ha).status_code == 404

    assert client.delete(f"/api/work-items/{a}", headers=ha).json() == {"message": "Deleted"}
    assert client.get(f"/api/work-items/{a}", headers=ha).status_code == 404
    assert client.get("/api/work-items", headers=hb).json()[0]["id"] == b
    assert client.get("/api/work-items", headers=ha).status_code == 200


def test_delete_stops_open_items_and_refuses_a_live_one(client, ha):
    uid = _uid(client, ha)
    db = SessionLocal()
    try:
        user = db.get(models.User, uid)
        convo = models.Conversation(user_id=uid, title="t")
        db.add(convo)
        db.commit()
        waiting = models.WorkItem(user_id=uid, conversation_id=convo.id, instruction="x", title="Waiting job",
                                  status="waiting", wait_reason="approval")
        live = models.WorkItem(user_id=uid, conversation_id=convo.id, instruction="y", title="Live job",
                               status="running", lease_until=models.utcnow() + timedelta(minutes=3))
        dead = models.WorkItem(user_id=uid, conversation_id=convo.id, instruction="z", title="Dead job",
                               status="running", lease_until=models.utcnow() - timedelta(minutes=3))
        db.add_all([waiting, live, dead])
        db.commit()
        pa = models.PendingAction(user_id=uid, conversation_id=convo.id, work_item_id=waiting.id,
                                  tool_name="send_email", arguments={}, preview="Send", status="pending")
        db.add(pa)
        db.commit()
        ids = (waiting.id, live.id, dead.id, pa.id)
    finally:
        db.close()
    waiting_id, live_id, dead_id, pa_id = ids

    busy = client.delete(f"/api/work-items/{live_id}", headers=ha)
    assert busy.status_code == 409
    assert busy.json()["work_item_id"] == live_id and busy.json()["status"] == "running"
    assert client.get(f"/api/work-items/{live_id}", headers=ha).status_code == 200

    assert client.delete(f"/api/work-items/{waiting_id}", headers=ha).status_code == 200
    assert client.delete(f"/api/work-items/{dead_id}", headers=ha).status_code == 200
    db = SessionLocal()
    try:
        assert db.get(models.WorkItem, waiting_id) is None
        pa = db.get(models.PendingAction, pa_id)
        assert pa is not None and pa.status == "rejected" and pa.work_item_id is None  # the card is closed, not stranded
    finally:
        db.close()


def test_task_types(client, ha):
    out = client.get("/api/task-types", headers=ha).json()
    assert out[0] == {"value": "email", "label": "Email"}
    assert {t["value"] for t in out} >= {"email", "scheduling", "todo", "other"} and "general" not in {t["value"] for t in out}


def test_preference_crud(client, ha, hb):
    made = client.post("/api/preferences", headers=ha,
                       json={"text": "Keep replies under 120 words.", "task_type": "email"})
    assert made.status_code == 200
    p = made.json()
    assert set(p) == {"id", "task_type", "system_scope", "text", "source", "origin", "active", "superseded_by",
                      "use_count", "last_used_at", "created_at"}
    assert (p["task_type"], p["source"], p["origin"], p["active"]) == ("email", "stated", "settings", True)

    # a near-twin is not added twice; a close rewrite replaces the old line
    again = client.post("/api/preferences", headers=ha, json={"text": "keep replies under 120 words", "task_type": "email"})
    assert again.json()["id"] == p["id"]
    newer = client.post("/api/preferences", headers=ha,
                        json={"text": "Keep replies under 80 words.", "task_type": "email"}).json()
    assert newer["id"] != p["id"]
    listed = {r["id"] for r in client.get("/api/preferences?task_type=email", headers=ha).json()}
    assert newer["id"] in listed and p["id"] not in listed
    everything = client.get("/api/preferences?include_inactive=true", headers=ha).json()
    old = next(r for r in everything if r["id"] == p["id"])
    assert old["active"] is False and old["superseded_by"] == newer["id"]

    edited = client.patch(f"/api/preferences/{newer['id']}", headers=ha,
                          json={"text": "Keep replies short.", "task_type": "general", "system_scope": "EM"})
    assert edited.status_code == 200 and edited.json()["text"] == "Keep replies short."
    assert (edited.json()["task_type"], edited.json()["system_scope"]) == ("general", "EM")
    off = client.patch(f"/api/preferences/{newer['id']}", headers=ha, json={"active": False})
    assert off.json()["active"] is False
    assert newer["id"] not in {r["id"] for r in client.get("/api/preferences", headers=ha).json()}
    assert client.patch(f"/api/preferences/{newer['id']}", headers=ha, json={"text": " "}).status_code == 422
    assert client.post("/api/preferences", headers=ha, json={"text": "   ", "task_type": "email"}).status_code == 422
    assert client.post("/api/preferences", headers=ha, json={"text": "x" * 301}).status_code == 422

    # secrets never reach the store
    leaked = client.post("/api/preferences", headers=ha,
                         json={"text": "Use the key sk-abcdefghijklmnopqrstuvwxyz123456 for uploads", "task_type": "files"})
    assert "sk-abcdefghijkl" not in leaked.json()["text"]

    assert client.patch(f"/api/preferences/{newer['id']}", headers=hb, json={"active": True}).status_code == 404
    assert client.delete(f"/api/preferences/{newer['id']}", headers=hb).status_code == 404
    assert client.delete(f"/api/preferences/{newer['id']}", headers=ha).json() == {"message": "Deleted"}
    assert client.delete(f"/api/preferences/{newer['id']}", headers=ha).status_code == 404


def test_memory_edit_route(client, ha, hb):
    made = client.post("/api/memories", headers=ha, json={"content": "Priya is in London", "kind": "contact"}).json()
    # the 1.0 kinds are still accepted from older clients and filed as facts
    legacy = client.post("/api/memories", headers=ha, json={"content": "Likes tea", "kind": "preference"})
    assert legacy.status_code == 200 and legacy.json()["kind"] == "fact"
    out = client.patch(f"/api/memories/{made['id']}", headers=ha,
                       json={"content": "Priya is in Paris now", "kind": "fact", "pinned": True})
    assert out.status_code == 200
    assert (out.json()["content"], out.json()["kind"], out.json()["pinned"], out.json()["source"]) == (
        "Priya is in Paris now", "fact", True, "manual")
    for bad in ({"content": ""}, {"content": "x" * 2001}, {"kind": "preference"}, {"kind": "style"}):
        assert client.patch(f"/api/memories/{made['id']}", headers=ha, json=bad).status_code == 422
    assert client.patch(f"/api/memories/{made['id']}", headers=hb, json={"pinned": False}).status_code == 404
    assert client.patch("/api/memories/nope", headers=ha, json={"pinned": False}).status_code == 404


def test_memory_settings_pause(client, hb):
    assert client.get("/api/memory/settings", headers=hb).json() == {"paused": False}
    assert client.patch("/api/memory/settings", headers=hb, json={"paused": True}).json() == {"paused": True}
    assert client.get("/api/memory/settings", headers=hb).json() == {"paused": True}
    db = SessionLocal()
    try:
        row = db.scalars(select(models.UserSettings).where(models.UserSettings.user_id == _uid(client, hb))).first()
        assert row.memory_paused is True
    finally:
        db.close()
    assert client.patch("/api/memory/settings", headers=hb, json={}).status_code == 422
    assert client.patch("/api/memory/settings", headers=hb, json={"paused": False}).json() == {"paused": False}


def test_conversation_memory_off_and_chat_in_flag(client, ha, hb):
    convo = client.post("/api/chat/conversations", headers=ha).json()
    assert convo["memory_off"] is False
    on = client.patch(f"/api/chat/conversations/{convo['id']}/memory", headers=ha, json={"memory_off": True})
    assert on.status_code == 200 and on.json()["memory_off"] is True and on.json()["id"] == convo["id"]
    assert [c for c in client.get("/api/chat/conversations", headers=ha).json() if c["id"] == convo["id"]][0]["memory_off"]
    assert client.patch(f"/api/chat/conversations/{convo['id']}/memory", headers=hb,
                        json={"memory_off": False}).status_code == 404
    assert client.patch(f"/api/chat/conversations/{convo['id']}/memory", headers=ha, json={}).status_code == 422
    client.patch(f"/api/chat/conversations/{convo['id']}/memory", headers=ha, json={"memory_off": False})

    # the very first message of a new chat can opt out: the flag is set before the run starts
    seen = []
    from app.agent import coordinator

    real = coordinator.iter_agent

    def spy(db, user, conversation, *a, **kw):
        seen.append(conversation.memory_off)
        return real(db, user, conversation, *a, **kw)

    coordinator.iter_agent = spy
    try:
        r = client.post("/api/chat/stream", headers=ha, json={"message": "hello there", "memory_off": True})
        assert r.status_code == 200
        r2 = client.post("/api/chat", headers=ha, json={"message": "and again", "memory_off": True,
                                                         "conversation_id": convo["id"]})
        assert r2.status_code == 200
        r3 = client.post("/api/chat", headers=ha, json={"message": "no flag", "conversation_id": convo["id"]})
        assert r3.status_code == 200
    finally:
        coordinator.iter_agent = real
    assert seen == [True, True, True]  # omitted leaves the flag as the previous message set it
    convo_after = client.get(f"/api/chat/conversations/{convo['id']}", headers=ha).json()
    assert convo_after["memory_off"] is True


def test_reset_scopes(client, ha, hb):
    def seed(h):
        db = SessionLocal()
        try:
            user = db.get(models.User, _uid(client, h))
            prefs.add(db, user, "Always cc the assistant", "email", "stated", "settings")
            prefs.add(db, user, "Use British spelling", "general", "stated", "settings")
            mem.remember(db, user.id, "Priya leads the Series A round", kind="fact")
            finished_item(db, user, title="Done job")
            running = models.WorkItem(user_id=user.id, instruction="x", title="Open job", status="ready")
            db.add(running)
            db.commit()
            return running.id
        finally:
            db.close()

    def counts(h):
        db = SessionLocal()
        try:
            uid = _uid(client, h)
            return (db.query(Preference).filter_by(user_id=uid).count(), db.query(WorkItem).filter_by(user_id=uid).count(),
                    db.query(Memory).filter_by(user_id=uid).count())
        finally:
            db.close()

    for h in (ha, hb):
        _clear = client.post("/api/memory/reset", headers=h, json={"scope": "all", "confirm": "RESET"})
        assert _clear.status_code == 200
    seed(hb)  # another account's data must survive every reset below
    open_id = seed(ha)
    base_b = counts(hb)

    # no confirmation, a wrong word, an unknown scope: nothing is deleted
    for body in ({"scope": "all"}, {"scope": "all", "confirm": "reset"}, {"scope": "all", "confirm": ""},
                 {"scope": "nonsense", "confirm": "RESET"}, {"confirm": "RESET"}):
        assert client.post("/api/memory/reset", headers=ha, json=body).status_code == 422
    assert counts(ha) == (2, 3, 1)  # the done one, the ready one, and the live one left by the delete test

    r = client.post("/api/memory/reset", headers=ha, json={"scope": "preferences", "confirm": "RESET"})
    assert r.json() == {"deleted": {"preferences": 2, "work_items": 0, "memories": 0}}
    assert counts(ha) == (0, 3, 1)
    r = client.post("/api/memory/reset", headers=ha, json={"scope": "task_memory", "confirm": "RESET"})
    assert r.json() == {"deleted": {"preferences": 0, "work_items": 1, "memories": 0}}  # the open one is kept
    db = SessionLocal()
    try:
        assert db.get(models.WorkItem, open_id) is not None
    finally:
        db.close()
    r = client.post("/api/memory/reset", headers=ha, json={"scope": "facts", "confirm": "RESET"})
    assert r.json() == {"deleted": {"preferences": 0, "work_items": 0, "memories": 1}}
    seed(ha)
    r = client.post("/api/memory/reset", headers=ha, json={"scope": "all", "confirm": "RESET"})
    assert r.json()["deleted"] == {"preferences": 2, "work_items": 1, "memories": 1}  # open items stay
    assert counts(hb) == base_b


def test_skill_teach_and_notes_endpoints(client, ha):
    code = "EM01"
    taught = client.post(f"/api/skills/{code}/teach", headers=ha, json={"note": "Sign off with just my first name"})
    assert taught.status_code == 200 and taught.json()["learned_notes"] == ["Sign off with just my first name"]
    stored = [p for p in client.get("/api/preferences?task_type=email", headers=ha).json() if p["origin"] == "skill_teach"]
    assert stored and stored[0]["source"] == "stated"
    assert client.post(f"/api/skills/{code}/teach", headers=ha, json={"note": "x"}).status_code == 422
    assert client.post("/api/skills/ZZ99/teach", headers=ha, json={"note": "hello there"}).status_code == 404
    assert client.delete(f"/api/skills/{code}/notes", headers=ha).json() == {"message": "Cleared"}
    skill = next(s for s in client.get("/api/skills", headers=ha).json() if s["code"] == code)
    assert skill["learned_notes"] == []
    inactive = [p for p in client.get("/api/preferences?include_inactive=true", headers=ha).json()
                if p["origin"] == "skill_teach"]
    assert inactive and not any(p["active"] for p in inactive)  # switched off, still listed
    assert client.delete("/api/skills/ZZ99/notes", headers=ha).status_code == 404
