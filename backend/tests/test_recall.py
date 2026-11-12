"""Recalling similar past work, and the context cap (spec 20 sections 9.4, 9.5)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402

from app.models import Memory, Preference, utcnow  # noqa: E402
from app.services import memory as memory_service  # noqa: E402
from app.services import preferences as prefs  # noqa: E402
from app.services import workmemory as wm  # noqa: E402
from tests.work_helpers import FakeProvider, conversation, finished_item, make_session, make_user  # noqa: E402


@pytest.fixture
def env(tmp_path):
    engine, db = make_session(tmp_path)
    user = make_user(db)
    yield db, user
    db.close()
    engine.dispose()


def test_second_similar_task_recalls_first_and_preference(env):
    """The data-layer half of the end-to-end story: what turn 3 would be shown."""
    db, user = env
    # turn 1: an assignment is recorded and closed
    one = wm.create_item(db, user, "add a task to call Priya about the Q3 report")
    wm.set_plan(db, one, "Add a task: call Priya about Q3", "todo", ["Create the task"])
    wm.add_step(db, one, kind="tool", tool_name="create_task", status="ok", summary="Created 'Call Priya about Q3 report'")
    wm.note_tool(db, one, "create_task", "core-tasks")
    wm.finalise(db, one, status="done", report="Added the task 'Call Priya about the Q3 report'.", summary="Created the task.")
    # turn 2: a preference
    noted = prefs.capture(db, user, None, "From now on, always mark Priya tasks as high priority", turn_type="todo")
    assert noted[0].preference.task_type == "todo"
    # turn 3, a new conversation
    query = "add a task to call Priya about the Q4 report"
    recall = wm.build_recall(db, user, query, "todo", systems={"core-tasks"})
    assert wm.PAST_HEADER in recall.block
    assert "Add a task: call Priya about Q3" in recall.block
    assert "always mark Priya tasks as high priority" in recall.block
    assert [w.id for w in recall.work] == [one.id] and [p.id for p in recall.preferences] == [noted[0].preference.id]
    assert recall.counts() == {"work_items": 1, "preferences": 1, "memories": 0}
    three = wm.create_item(db, user, query)
    wm.record_recall(db, three, recall)
    db.refresh(three)
    assert three.recall_used == {"work_item_ids": [one.id], "preference_ids": [noted[0].preference.id], "memory_ids": []}
    resolved = wm.resolve_recall(db, three)
    assert resolved["past_work"][0]["id"] == one.id and resolved["past_work"][0]["title"].startswith("Add a task")
    assert resolved["preferences"] == [{"id": noted[0].preference.id, "task_type": "todo",
                                        "text": noted[0].preference.text}]
    # the running item itself is never recalled for itself
    assert wm.recall_work(db, user.id, query, "todo", exclude_id=one.id) == []


def test_ranking_threshold_and_scope(env):
    db, user = env
    match = finished_item(db, user, title="Send Priya the Q3 numbers", task_type="email", status="done")
    typed_only = finished_item(db, user, title="Weekly newsletter draft", task_type="email", status="done")
    unrelated = finished_item(db, user, title="Book dentist appointment", task_type="scheduling")
    hidden = finished_item(db, user, title="Send Priya the Q3 numbers again", task_type="email", recallable=False)
    open_ = finished_item(db, user, title="Send Priya the Q3 numbers now", task_type="email", status="running")
    other = make_user(db, email="other@example.com")
    foreign = finished_item(db, other, title="Send Priya the Q3 numbers", task_type="email")
    hits = wm.recall_work(db, user.id, "send Priya the Q3 numbers", "email")
    ids = [h.item.id for h in hits]
    assert ids[0] == match.id
    assert unrelated.id not in ids and hidden.id not in ids and open_.id not in ids and foreign.id not in ids
    assert typed_only.id not in ids, "no words in common: the type and status bonuses alone recall nothing"
    assert all(h.score >= wm.MIN_SCORE for h in hits)


def test_at_most_three_newer_first_on_a_tie(env):
    db, user = env
    items = [finished_item(db, user, title="Send Priya the Q3 numbers", task_type="email",
                           age=timedelta(days=d)) for d in (5, 1, 3, 2)]
    hits = wm.recall_work(db, user.id, "send Priya the Q3 numbers", "email")
    assert len(hits) == 3
    assert [h.item.id for h in hits] == [items[1].id, items[3].id, items[2].id]


def test_feedback_changes_rank(env):
    db, user = env
    liked = finished_item(db, user, title="Draft the board update", task_type="writing", feedback={"rating": "up"})
    plain = finished_item(db, user, title="Draft the board update", task_type="writing", age=timedelta(minutes=5))
    disliked = finished_item(db, user, title="Draft the board update", task_type="writing", feedback={"rating": "down", "note": "too long"},
                             age=timedelta(minutes=10))
    partly = finished_item(db, user, title="Draft the board update", task_type="writing", status="partly_done",
                           age=timedelta(minutes=20))
    hits = wm.recall_work(db, user.id, "draft the board update", "writing")
    assert [h.item.id for h in hits][:2] == [liked.id, plain.id]
    scores = {h.item.id: h.score for h in wm.recall_work(db, user.id, "draft the board update", "writing", limit=10)}
    assert scores[liked.id] - scores[plain.id] == pytest.approx(0.05)
    assert scores[plain.id] - scores[disliked.id] == pytest.approx(0.10)
    assert scores[plain.id] - scores[partly.id] == pytest.approx(0.05)
    line = wm.format_line(disliked)
    assert 'User feedback: "too long".' in line and "partly" not in line
    assert "partly done" in wm.format_line(partly)


def test_memory_off_conversation_gets_no_recall(env):
    db, user = env
    finished_item(db, user, title="Send Priya the Q3 numbers", task_type="email")
    prefs.add(db, user, "Always keep email replies short", "email", "stated", "settings")
    db.add(Memory(user_id=user.id, kind="fact", content="Priya leads the Series A", source="chat"))
    db.commit()
    convo = conversation(db, user, memory_off=True)
    assert wm.recall_work(db, user.id, "send Priya the Q3 numbers", "email", memory_off=True) == []
    empty = wm.build_recall(db, user, "send Priya the Q3 numbers", "email", conversation=convo)
    assert empty.block == "" and empty.used() == {"work_item_ids": [], "preference_ids": [], "memory_ids": []}
    on = wm.build_recall(db, user, "send Priya the Q3 numbers", "email", conversation=conversation(db, user))
    assert on.block and on.counts() == {"work_items": 1, "preferences": 1, "memories": 1}


def test_a_blank_query_recalls_no_work_but_still_gives_preferences(env):
    db, user = env
    finished_item(db, user, title="Send Priya the Q3 numbers", task_type="email")
    prefs.add(db, user, "Be brief", "general", "stated", "settings")
    r = wm.build_recall(db, user, "   ", "other")
    assert r.work == [] and "Be brief" in r.block


def test_recall_block_within_4000_chars(env):
    db, user = env
    for i in range(6):
        finished_item(db, user, title="Send Priya the quarterly numbers " + "word " * 60, task_type="email",
                      summary="long " * 200, age=timedelta(days=i + 1))
    for i in range(14):
        prefs.add(db, user, f"Rule {i} " + "x" * 120 + f" unique{i}", "email" if i % 2 else "general", "stated", "settings")
    for i in range(8):
        db.add(Memory(user_id=user.id, kind="fact", content=f"Priya quarterly numbers fact {i} " + "y" * 400, source="chat"))
    db.commit()
    r = wm.build_recall(db, user, "send Priya the quarterly numbers", "email")
    assert len(r.block) <= wm.CONTEXT_CAP
    assert len(r.work) == 3 and r.work, "past work is kept while facts can still be dropped"
    assert len(r.memory_ids) < 6
    typed = [p for p in r.preferences if p.task_type == "email"]
    assert typed, "type-specific preferences are dropped last"
    assert all(len(line) <= wm.LINE_MAX for line in r.block.splitlines() if line.startswith("- [20"))


def test_over_the_cap_drops_facts_then_work_then_general_then_typed(env):
    db, user = env
    finished_item(db, user, title="Send Priya the numbers", task_type="email")
    typed = prefs.add(db, user, "Typed rule " + "t" * 200, "email", "stated", "settings").preference
    general = prefs.add(db, user, "General rule " + "g" * 200, "general", "stated", "settings").preference
    db.add(Memory(user_id=user.id, kind="fact", content="Priya numbers fact", source="chat"))
    db.commit()
    full = wm.build_recall(db, user, "send Priya the numbers", "email")
    assert (len(full.work), len(full.preferences), len(full.memory_ids)) == (1, 2, 1)
    gone_at: dict[str, int] = {}
    for cap in range(len(full.block), 0, -5):
        r = wm.build_recall(db, user, "send Priya the numbers", "email", cap=cap)
        assert len(r.block) <= cap
        present = {"fact": bool(r.memory_ids), "work": bool(r.work),
                   "general": general.id in [p.id for p in r.preferences], "typed": typed.id in [p.id for p in r.preferences]}
        for name, here in present.items():
            if not here:
                gone_at.setdefault(name, cap)  # the largest cap at which it is already missing
            else:
                assert name not in gone_at, f"{name} came back at cap {cap}"
    assert gone_at["fact"] > gone_at["work"] > gone_at["general"] > gone_at["typed"], gone_at


def test_format_line_is_capped(env):
    db, user = env
    item = finished_item(db, user, title="T" * 190, summary="s" * 900, feedback={"rating": "down", "note": "n" * 300})
    line = wm.format_line(item)
    assert len(line) <= wm.LINE_MAX and line.startswith("- [") and "· other · done]" in line
    assert line.endswith('"' + "n" * 80 + '".')


def test_lazy_embedding_when_the_provider_can_embed(env, monkeypatch):
    db, user = env

    def vec(text: str) -> list[float]:
        return [1.0, 0.0] if "priya" in text.lower() else [0.0, 1.0]

    items = [finished_item(db, user, title=f"Send Priya number {i}", task_type="email") for i in range(7)]
    far = finished_item(db, user, title="Book the dentist", task_type="scheduling", embedding=[0.0, 1.0])
    monkeypatch.setattr(memory_service, "get_provider", lambda: FakeProvider(vectors=vec))
    hits = wm.recall_work(db, user.id, "send priya", "email", limit=10)
    embedded = [i for i in items if i.embedding]
    assert len(embedded) == wm.LAZY_EMBED, "at most five per pass"
    assert far.id not in [h.item.id for h in hits]
    wm.recall_work(db, user.id, "send priya", "email")
    assert all(i.embedding for i in items), "the rest converge on the next pass"


def test_lexical_scoring_when_a_vector_is_missing(env, monkeypatch):
    db, user = env
    item = finished_item(db, user, title="Send Priya the Q3 numbers", task_type="email")
    monkeypatch.setattr(memory_service, "get_provider", lambda: FakeProvider(vectors=lambda t: [1.0, 0.0]))
    # the query has a vector, the item has none yet: it is embedded lazily, and found either way
    assert [h.item.id for h in wm.recall_work(db, user.id, "send Priya the Q3 numbers", "email")] == [item.id]
    monkeypatch.setattr(memory_service, "get_provider", lambda: FakeProvider())
    item.embedding = [1.0, 0.0]
    db.commit()
    assert [h.item.id for h in wm.recall_work(db, user.id, "send Priya the Q3 numbers", "email")] == [item.id]


def test_preference_use_is_counted_once_per_turn(env):
    db, user = env
    p = prefs.add(db, user, "Be brief", "general", "stated", "settings").preference
    wm.build_recall(db, user, "anything", "email")
    db.refresh(p)
    assert p.use_count == 1 and p.last_used_at is not None
