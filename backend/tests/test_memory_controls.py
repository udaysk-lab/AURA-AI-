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
