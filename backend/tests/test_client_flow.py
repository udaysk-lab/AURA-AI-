"""The web client's flows, end to end through the HTTP API (client audit, AURA 2.0).

Each test follows the sequence of calls frontend/lib/api.ts makes for one thing a person does, with
the scripted model standing in for the provider, and asserts the fields the client's types read
(`Message.meta`, `PendingAction`, `WorkItemDetail`, `PreferenceItem`, `PermissionRule`, ...).
Sign-up flows run as a hosted site; the folder-access flow runs as a local install."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("ALLOW_DEMO_LOGIN", "true")

import itertools  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import deps  # noqa: E402
from app.agent import coordinator  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import engine  # noqa: E402
from app.device.runtime import reset_cache  # noqa: E402
from app.main import app  # noqa: E402
from app.permissions import access  # noqa: E402
from app.services import mailer  # noqa: E402
from tests.schema import drop_test_schema, ensure_test_schema  # noqa: E402
from tests.scripted import ScriptedProvider, call, reply  # noqa: E402

PASSWORD = "correct horse 9"
SEND = dict(to="priya@example.com", subject="Q3 numbers", body="The numbers are attached.")

# What the client's TypeScript types require (frontend/lib/api.ts), field by field.
WORK_ITEM = {"id", "title", "task_type", "status", "trigger", "conversation_id", "outcome", "steps_total",
             "cost_usd", "recallable", "created_at", "finished_at"}
WORK_DETAIL = WORK_ITEM | {"instruction", "plan", "systems_used", "tools_used", "summary", "report",
                           "user_feedback", "pending_action_ids", "steps", "recall_used"}
WORK_STEP = {"id", "seq", "step_no", "kind", "tool_name", "system", "status", "summary", "detail",
             "result_summary", "started_at", "finished_at", "pending_action_id"}
PENDING = {"id", "tool_name", "skill_code", "arguments", "preview", "status", "created_at", "card", "options",
           "expires_at", "origin", "reason", "work_item_id", "conversation_id", "card_payload", "always_user"}
PREFERENCE = {"id", "task_type", "system_scope", "text", "source", "origin", "active", "superseded_by",
              "use_count", "last_used_at", "created_at"}
RULE = {"id", "effect", "system", "tool", "target_kind", "pattern", "applies_to", "source", "expires_at",
        "revoked_at", "note", "created_at"}
STATE = {"mode", "mode_label", "auto_available", "auto_effective", "reviewer_model", "boundaries"}
CONVERSATION = {"id", "title", "pinned", "memory_off", "created_at", "updated_at"}
MESSAGE = {"id", "role", "content", "meta", "created_at"}
FOLDER = {"id", "path", "can_delete", "source", "created_at"}


@pytest.fixture(scope="module")
def client(as_peer):
    drop_test_schema(engine)
    ensure_test_schema(engine)
    with TestClient(as_peer(app), base_url="http://127.0.0.1") as c:
        yield c
    drop_test_schema(engine)


@pytest.fixture(autouse=True)
def hosted(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "local_mode", False, raising=False)
    monkeypatch.setattr(settings, "device_control", "", raising=False)
    monkeypatch.setattr(settings, "aura_home", str(tmp_path / "aura-home"), raising=False)
    monkeypatch.setattr(settings, "llm_provider", "mock", raising=False)
    reset_cache()
    access.reset_state()
    yield
    access.reset_state()
    reset_cache()


@pytest.fixture
def sent(monkeypatch):
    """Every email that really goes out (none, unless the owner said yes)."""
    out: list[dict] = []

    def send(db, user, to, subject, body):
        out.append({"to": to, "subject": subject})
        return {"sent": True, "to": to, "route": "test"}

    monkeypatch.setattr(mailer, "send", send)
    return out


_n = itertools.count(1)


def sign_up(client, name="Asha"):
    """The sign-up screen: POST /api/auth/register, then the token goes on every call."""
    email = f"flow{next(_n)}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "name": name})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["access_token"] and body["user"]["email"] == email and body["user"]["name"] == name
    assert {"id", "email", "name", "avatar_url", "timezone", "is_demo"} <= set(body["user"])
    return {"Authorization": f"Bearer {body['access_token']}"}


def script(monkeypatch, *responses):
    provider = ScriptedProvider(list(responses))
    monkeypatch.setattr(coordinator, "get_provider", lambda: provider)
    return provider


def stream(client, h, message, conversation_id=None, **extra):
    """api.streamChat: POST /api/chat/stream, read `data:` frames until [DONE]."""
    r = client.post("/api/chat/stream", headers=h,
                    json={"message": message, "conversation_id": conversation_id, **extra})
    return frames(r)


def carry_on(client, h, work_item_id):
    """api.continueChat."""
    r = client.post("/api/chat/continue", headers=h, json={"work_item_id": work_item_id})
    return frames(r)


def frames(r):
    assert r.status_code == 200, r.text
    assert r.text.rstrip().endswith("data: [DONE]")
    events = []
    for line in r.text.splitlines():
        if line.startswith("data:") and line[5:].strip() != "[DONE]":
            events.append(json.loads(line[5:]))
    return events


def of(events, kind):
    return [e for e in events if e["type"] == kind]


def done_of(events):
    (done,) = of(events, "done")
    assert {"conversation_id", "message_id", "latency_ms", "steps", "work_item_id", "work_status"} <= set(done)
    return done


def conversation(client, h, conversation_id):
    """api.conversation: what the chat page shows when the chat is opened."""
    r = client.get(f"/api/chat/conversations/{conversation_id}", headers=h)
    assert r.status_code == 200, r.text
    convo = r.json()
    assert CONVERSATION <= set(convo)
    for m in convo["messages"]:
        assert MESSAGE <= set(m)
    return convo


def pending(client, h):
    r = client.get("/api/pending-actions", headers=h)
    assert r.status_code == 200
    return r.json()


# ---------------------------------------------------------------------------
# (a) Sign up and ask a quick question
# ---------------------------------------------------------------------------


def test_a_sign_up_and_quick_question(client, monkeypatch):
    h = sign_up(client)
    me = client.get("/api/auth/me", headers=h).json()
    assert me["name"] == "Asha"
    # A fresh account has a mode and a conversation list the client can read.
    assert client.get("/api/permissions", headers=h).json()["mode"] == "ask"
    assert client.get("/api/chat/conversations", headers=h).json() == []
    assert client.get("/api/memory/settings", headers=h).json() == {"paused": False}

    script(monkeypatch, reply("It is a quarter of the year, so three months."))
    events = stream(client, h, "How long is a quarter?")
    assert events[0]["type"] == "start" and events[0]["conversation_id"]
    kinds = [e["type"] for e in events]
    assert "delta" in kinds and kinds[-1] == "done"
    for gone in ("tool_start", "skill", "pending_action", "learned", "memory"):
        assert gone not in kinds
    assert "".join(e["value"] for e in of(events, "delta")) == "It is a quarter of the year, so three months."
    done = done_of(events)
    assert done["work_item_id"] is None and done["work_status"] is None
    assert not of(events, "task") and not of(events, "report")

    convo = conversation(client, h, done["conversation_id"])
    assert [m["role"] for m in convo["messages"]] == ["user", "assistant"]
    assert convo["messages"][1]["content"].startswith("It is a quarter")
    assert convo["messages"][1]["meta"].get("work_item_id") in (None, "")
    listed = client.get("/api/chat/conversations", headers=h).json()
    assert [c["id"] for c in listed] == [done["conversation_id"]]
    assert listed[0]["title"] == "How long is a quarter?" and listed[0]["memory_off"] is False
    assert client.get("/api/work-items", headers=h).json() == []
    assert pending(client, h) == []


# ---------------------------------------------------------------------------
# (b) A task that changes the workspace, without asking
# ---------------------------------------------------------------------------


def test_b_workspace_change_runs_without_asking(client, monkeypatch):
    h = sign_up(client)
    script(monkeypatch,
           reply("", call("create_task", title="Call Priya about Q3")),
           reply("Added 'Call Priya about Q3' to your tasks."))
    events = stream(client, h, "add a task to call Priya about Q3")
    kinds = [e["type"] for e in events]
    assert kinds.index("task") < kinds.index("step_started") < kinds.index("step_finished") < kinds.index("report")
    assert "approval_needed" not in kinds and "waiting" not in kinds
    (task,) = of(events, "task")
    assert {"work_item_id", "title", "task_type", "status", "resumed"} <= set(task) and task["resumed"] is False
    (started,) = of(events, "step_started")
    assert {"work_item_id", "step_no", "seq", "tool", "system", "label"} <= set(started)
    assert started["tool"] == "create_task" and started["system"] == "core-tasks"
    (finished,) = of(events, "step_finished")
    assert {"work_item_id", "step_no", "seq", "tool", "ok", "summary", "code"} <= set(finished)
    assert finished["ok"] is True and finished["seq"] == started["seq"]
    (report,) = of(events, "report")
    assert report["status"] == "done" and report["verification"] == "passed" and report["title"]
    done = done_of(events)
    assert done["work_item_id"] == task["work_item_id"] and done["work_status"] == "done"

    assert pending(client, h) == []
    tasks = client.get("/api/tasks?status=all", headers=h).json()
    assert "Call Priya about Q3" in [t["title"] for t in tasks]  # a new account also has sample tasks
    item = client.get(f"/api/work-items/{done['work_item_id']}", headers=h).json()
    assert WORK_DETAIL <= set(item) and item["status"] == "done" and item["trigger"] == "chat"
    assert item["tools_used"] == ["create_task"] and item["systems_used"] == ["core-tasks"]
    assert item["verification"]["status"] == "passed" and item["pending_action_ids"] == []
    assert [s["kind"] for s in item["steps"] if s["kind"] == "tool"] == ["tool"]
    # the message in the opened chat carries what the client reads from meta
    msg = conversation(client, h, done["conversation_id"])["messages"][-1]
    assert msg["meta"]["work_item_id"] == item["id"] and msg["meta"]["work_status"] == "done"
    assert msg["meta"]["preferences_noted"] == []
    (run,) = msg["meta"]["skill_runs"]  # the chat's skill lines (LiveSkill)
    assert {"code", "name", "summary", "ok"} <= set(run) and run["ok"] is True and run["code"] == "TK01"
    # and the item is listed for the memory page
    (row,) = client.get("/api/work-items", headers=h).json()
    assert WORK_ITEM <= set(row) and row["id"] == item["id"] and row["steps_total"] >= 1


# ---------------------------------------------------------------------------
# (c) A task that needs approval: allow once
# ---------------------------------------------------------------------------


def test_c_approval_allow_once_then_report(client, monkeypatch, sent):
    h = sign_up(client)
    pref = client.post("/api/preferences", headers=h,
                       json={"text": "Sign emails to colleagues with my first name only.",
                             "task_type": "email", "system_scope": ""})
    assert pref.status_code == 200, pref.text
    script(monkeypatch,
           reply("", call("send_email", **SEND)),
           reply("Sent the Q3 numbers to Priya."))
    events = stream(client, h, "email priya the Q3 numbers")
    kinds = [e["type"] for e in events]
    assert kinds.index("task") < kinds.index("approval_needed") < kinds.index("waiting") < kinds.index("done")
    assert "report" not in kinds and "step_started" not in kinds
    (need,) = of(events, "approval_needed")
    assert {"work_item_id", "pending_action_id", "tool_name", "skill_code", "preview", "expires_at"} <= set(need)
    (wait,) = of(events, "waiting")
    assert wait["pending_action_ids"] == [need["pending_action_id"]] and wait["expires_at"]
    done = done_of(events)
    assert done["work_status"] == "waiting" and done["work_item_id"] == need["work_item_id"]
    assert sent == []

    # The card, read from the pending-actions route (api.pendingActions / pendingAction).
    (card,) = pending(client, h)
    assert PENDING <= set(card)
    assert card["id"] == need["pending_action_id"] and card["tool_name"] == "send_email"
    assert card["card"] == "tool" and card["always_user"] is False and card["origin"] == "chat"
    assert card["status"] == "pending" and card["work_item_id"] == done["work_item_id"]
    assert card["conversation_id"] == done["conversation_id"]
    assert card["options"] == ["once", "window", "always", "reject"] and card["expires_at"]
    assert "priya@example.com" in card["preview"] and card["reason"]
    assert "priya@example.com" in card["card_payload"]["rule_text"]
    assert card["arguments"]["to"] == "priya@example.com"
    assert client.get(f"/api/pending-actions/{card['id']}", headers=h).json()["id"] == card["id"]

    # The task panel's view while it waits.
    waiting = client.get(f"/api/work-items/{done['work_item_id']}", headers=h).json()
    assert waiting["status"] == "waiting" and waiting["pending_action_ids"] == [card["id"]]
    assert [s["status"] for s in waiting["steps"] if s["kind"] == "approval"] == ["waiting"]

    # Allow once (api.decidePending) -> the task is ready to carry on.
    d = client.post(f"/api/pending-actions/{card['id']}", headers=h, json={"decision": "once"})
    assert d.status_code == 200, d.text
    outcome = d.json()
    assert outcome["status"] == "approved" and outcome["decision"] == "once"
    assert outcome["work_item"] == {"id": done["work_item_id"], "status": "ready"}
    assert sent == [{"to": "priya@example.com", "subject": "Q3 numbers"}]
    assert pending(client, h) == []
    assert client.post(f"/api/pending-actions/{card['id']}", headers=h, json={"decision": "once"}).status_code == 409

    # Continue (api.continueChat), as lib/taskrun.ts does.
    events = carry_on(client, h, done["work_item_id"])
    assert events[0] == {"type": "start", "conversation_id": done["conversation_id"]}
    (task,) = of(events, "task")
    assert task["resumed"] is True and task["work_item_id"] == done["work_item_id"]
    (report,) = of(events, "report")
    assert report["status"] == "done" and report["verification"] == "passed"
    assert "continue" not in [e["type"] for e in events]
    last = done_of(events)
    assert last["work_status"] == "done" and last["work_item_id"] == done["work_item_id"]
    assert "Sent the Q3 numbers" in "".join(e["value"] for e in of(events, "delta"))

    # A second continue (another tab) is a quiet 409 with the body the client reads.
    again_409 = client.post("/api/chat/continue", headers=h, json={"work_item_id": done["work_item_id"]})
    assert again_409.status_code == 409
    assert again_409.json()["work_item_id"] == done["work_item_id"] and again_409.json()["status"] == "done"
    assert again_409.json()["code"] == "not_ready"

    # The report, as the chat shows it once reopened.
    convo = conversation(client, h, done["conversation_id"])
    final = convo["messages"][-1]
    assert final["content"].startswith("Sent the Q3 numbers") and final["meta"]["work_status"] == "done"

    # The work item: steps and what was recalled.
    item = client.get(f"/api/work-items/{done['work_item_id']}", headers=h).json()
    assert WORK_DETAIL <= set(item) and item["status"] == "done" and item["task_type"] == "email"
    assert item["verification"]["status"] == "passed" and item["pending_action_ids"] == []
    for step in item["steps"]:
        assert WORK_STEP <= set(step)
    by_kind = {s["kind"]: s for s in item["steps"]}
    assert by_kind["approval"]["status"] == "approved" and by_kind["approval"]["pending_action_id"] == card["id"]
    assert by_kind["tool"]["status"] == "ok" and by_kind["tool"]["tool_name"] == "send_email"
    assert by_kind["tool"]["summary"] and by_kind["tool"]["result_summary"]
    used = item["recall_used"]
    assert set(used) == {"past_work", "preferences"}
    assert pref.json()["id"] in [p["id"] for p in used["preferences"]]
    assert {"id", "task_type", "text"} <= set(used["preferences"][0])

    # Asking the same thing again recalls the first one (past work carries id/title/outcome/finished_at).
    script(monkeypatch, reply("", call("send_email", **SEND)), reply("Done."))
    again = stream(client, h, "email priya the Q3 numbers")
    recall = of(again, "recall")
    assert recall and recall[0]["work_items"] >= 1 and recall[0]["preferences"] >= 1
    assert {"work_items", "preferences", "memories"} <= set(recall[0])
    second = done_of(again)["work_item_id"]
    past = client.get(f"/api/work-items/{second}", headers=h).json()["recall_used"]["past_work"]
    assert [p["id"] for p in past] == [done["work_item_id"]]
    assert {"id", "title", "outcome", "finished_at"} <= set(past[0])


# ---------------------------------------------------------------------------
# (d) The same, but the owner says no
# ---------------------------------------------------------------------------


def test_d_deny_the_approval(client, monkeypatch, sent):
    h = sign_up(client)
    script(monkeypatch,
           reply("", call("send_email", **SEND)),
           reply("Understood, I have not sent it."))
    events = stream(client, h, "email priya the Q3 numbers")
    done = done_of(events)
    assert done["work_status"] == "waiting"
    (card,) = pending(client, h)

    d = client.post(f"/api/pending-actions/{card['id']}", headers=h, json={"decision": "reject"})
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "rejected" and d.json()["decision"] == "reject"
    assert d.json()["work_item"] == {"id": done["work_item_id"], "status": "ready"}
    assert sent == [] and pending(client, h) == []

    events = carry_on(client, h, done["work_item_id"])
    (report,) = of(events, "report")
    assert report["status"] in ("done", "failed", "partly_done", "stopped")
    assert done_of(events)["work_status"] == report["status"]
    assert sent == []
    item = client.get(f"/api/work-items/{done['work_item_id']}", headers=h).json()
    assert item["status"] == report["status"] and item["pending_action_ids"] == []
    approval = [s for s in item["steps"] if s["kind"] == "approval"]
    assert [s["status"] for s in approval] == ["rejected"]
    assert not [s for s in item["steps"] if s["kind"] == "tool" and s["status"] == "ok"]
    # a second answer to the same card is refused, not applied twice
    assert client.post(f"/api/pending-actions/{card['id']}", headers=h, json={"decision": "once"}).status_code == 409
    assert sent == []


# ---------------------------------------------------------------------------
# (e) Permission mode and rules
# ---------------------------------------------------------------------------


def test_e_mode_and_rules(client, monkeypatch, sent):
    h = sign_up(client)
    state = client.get("/api/permissions", headers=h).json()
    assert STATE <= set(state) and state["mode"] == "ask" and state["mode_label"] == "Ask first"
    assert state["auto_available"] is False  # the keyword model cannot review actions

    r = client.put("/api/permissions/mode", headers=h, json={"mode": "skip"})
    assert r.status_code == 200, r.text
    assert STATE <= set(r.json()) and r.json()["mode"] == "skip" and r.json()["mode_label"] == "Skip approvals"
    assert client.get("/api/permissions", headers=h).json()["mode"] == "skip"
    assert client.put("/api/permissions/mode", headers=h, json={"mode": "auto"}).status_code == 409
    assert client.put("/api/permissions/mode", headers=h, json={"mode": "ask"}).json()["mode"] == "ask"
    b = client.put("/api/permissions/boundaries", headers=h, json={"text": "Never email clients."})
    assert b.status_code == 200 and b.json()["boundaries"] == "Never email clients."

    cat = client.get("/api/permissions/catalogue", headers=h).json()
    send = next(c for c in cat if c["tool"] == "send_email")
    assert {"tool", "system", "kind", "targets", "always_user"} <= set(send)

    # Add a rule (api.addPermissionRule) ...
    rule = {"effect": "allow", "system": send["system"], "tool": "send_email", "target_kind": "recipient",
            "pattern": "priya@example.com"}
    r = client.post("/api/permissions/rules", headers=h, json=rule)
    assert r.status_code in (200, 201), r.text
    made = r.json()
    assert RULE <= set(made) and made["effect"] == "allow" and made["source"] == "user" and made["id"]
    assert made["pattern"] == "priya@example.com" and made["revoked_at"] is None
    listed = client.get("/api/permissions/rules", headers=h).json()
    assert made["id"] in [x["id"] for x in listed]
    assert all(RULE <= set(x) or x.get("source") == "install" for x in listed)
    assert [x["id"] for x in client.get("/api/permissions/rules?effect=block", headers=h).json()
            if x.get("id") == made["id"]] == []

    # ... and it takes effect: the email goes without a card.
    script(monkeypatch, reply("", call("send_email", **SEND)), reply("Sent."))
    events = stream(client, h, "email priya the Q3 numbers")
    assert "approval_needed" not in [e["type"] for e in events] and done_of(events)["work_status"] == "done"
    assert len(sent) == 1

    # Change it, the way the client does: add the new rule, remove the old one (api.removePermissionRule) ...
    new = client.post("/api/permissions/rules", headers=h, json={**rule, "effect": "ask"})
    assert new.status_code in (200, 201) and new.json()["effect"] == "ask"
    gone = client.delete(f"/api/permissions/rules/{made['id']}", headers=h)
    assert gone.status_code == 200 and gone.json() == {"revoked": True}
    ids = [x["id"] for x in client.get("/api/permissions/rules", headers=h).json()]
    assert made["id"] not in ids and new.json()["id"] in ids
    # ... and the in-place way the API also offers.
    patched = client.patch(f"/api/permissions/rules/{new.json()['id']}", headers=h, json={"effect": "block"})
    assert patched.status_code == 200, patched.text
    assert RULE <= set(patched.json()) and patched.json()["effect"] == "block"
    # a block rule holds in every mode
    client.put("/api/permissions/mode", headers=h, json={"mode": "skip"})
    script(monkeypatch, reply("", call("send_email", **SEND)), reply("I was not allowed to send it."))
    events = stream(client, h, "email priya the Q3 numbers again")
    assert len(sent) == 1 and "approval_needed" not in [e["type"] for e in events]
    assert of(events, "step_finished")[0]["ok"] is False
    # Remove it; the log shows who did what and still verifies.
    final_id = patched.json()["id"]
    assert client.delete(f"/api/permissions/rules/{final_id}", headers=h).json() == {"revoked": True}
    assert final_id not in [x["id"] for x in client.get("/api/permissions/rules", headers=h).json()]
    log = client.get("/api/permissions/events?limit=100", headers=h).json()
    assert log and {"seq", "actor", "kind", "created_at"} <= set(log[0])
    assert {"mode_changed", "rule_added"} <= {e["kind"] for e in log}
    assert client.get("/api/permissions/events/verify", headers=h).json()["ok"] is True


# ---------------------------------------------------------------------------
# (f) Preferences: state one in chat, list it, edit it, turn it off
# ---------------------------------------------------------------------------


def test_f_preferences(client, monkeypatch):
    h = sign_up(client)
    statement = "From now on, always sign my emails to colleagues with just my first name."
    script(monkeypatch, reply("Understood. I will sign with your first name."))
    events = stream(client, h, statement)
    (noted,) = of(events, "preference_noted")
    assert {"id", "text", "task_type", "superseded_id"} <= set(noted) and noted["text"]
    done = done_of(events)
    assert done["work_item_id"] is None  # a statement is a quick turn, not a task

    # The chip the chat shows comes from the message meta once the chat is reopened.
    msg = conversation(client, h, done["conversation_id"])["messages"][-1]
    assert msg["meta"]["preferences_noted"] == [{"id": noted["id"], "text": noted["text"]}]

    # Memory page: list it (api.preferences) ...
    prefs = client.get("/api/preferences", headers=h).json()
    mine = next(p for p in prefs if p["id"] == noted["id"])
    assert PREFERENCE <= set(mine)
    assert mine["active"] is True and mine["source"] == "stated" and mine["task_type"] == noted["task_type"]
    assert client.get(f"/api/preferences?task_type={mine['task_type']}", headers=h).json()
    # ... edit it (api.updatePreference) ...
    r = client.patch(f"/api/preferences/{mine['id']}", headers=h,
                     json={"text": "Sign emails to colleagues with my first name, Asha."})
    assert r.status_code == 200, r.text
    assert PREFERENCE <= set(r.json()) and r.json()["text"].endswith("Asha.") and r.json()["active"] is True
    # ... and it is used the next time.
    prov = script(monkeypatch, reply("", call("create_task", title="Email Priya")), reply("Added."))
    stream(client, h, "add a task to email Priya")
    assert "with my first name, Asha" in json.dumps(prov.seen[0])
    # ... turn it off: it leaves the default list and appears with include_inactive.
    off = client.patch(f"/api/preferences/{mine['id']}", headers=h, json={"active": False})
    assert off.status_code == 200 and off.json()["active"] is False
    assert mine["id"] not in [p["id"] for p in client.get("/api/preferences", headers=h).json()]
    inactive = client.get("/api/preferences?include_inactive=true", headers=h).json()
    assert next(p for p in inactive if p["id"] == mine["id"])["active"] is False
    prov = script(monkeypatch, reply("", call("create_task", title="Email Dev")), reply("Added."))
    stream(client, h, "add a task to email Dev")
    assert "with my first name, Asha" not in json.dumps(prov.seen[0])
    # Turn it back on, then delete it.
    assert client.patch(f"/api/preferences/{mine['id']}", headers=h, json={"active": True}).json()["active"] is True
    assert client.delete(f"/api/preferences/{mine['id']}", headers=h).json() == {"message": "Deleted"}
    assert mine["id"] not in [p["id"] for p in client.get("/api/preferences?include_inactive=true", headers=h).json()]
    assert client.patch(f"/api/preferences/{mine['id']}", headers=h, json={"text": "x"}).status_code == 404


# ---------------------------------------------------------------------------
# (g) Pause memory, memory off for one chat, reset
# ---------------------------------------------------------------------------


def test_g_pause_and_reset_memory(client, monkeypatch):
    h = sign_up(client)
    # Pause (api.setMemoryPaused): nothing new is learned from chat.
    r = client.patch("/api/memory/settings", headers=h, json={"paused": True})
    assert r.status_code == 200 and r.json() == {"paused": True}
    assert client.get("/api/memory/settings", headers=h).json() == {"paused": True}
    before = {p["id"] for p in client.get("/api/preferences?include_inactive=true", headers=h).json()}
    script(monkeypatch, reply("", call("create_task", title="Water the plants")), reply("Added."))
    events = stream(client, h, "From now on, always put plants first in my task lists. Add a task: water the plants.")
    assert not of(events, "preference_noted")
    after = {p["id"] for p in client.get("/api/preferences?include_inactive=true", headers=h).json()}
    assert after == before
    item = client.get(f"/api/work-items/{done_of(events)['work_item_id']}", headers=h).json()
    assert item["recallable"] is False  # paused: kept as a record, not offered for recall
    assert client.patch("/api/memory/settings", headers=h, json={"paused": False}).json() == {"paused": False}

    # Memory off for one chat (api.setConversationMemoryOff), then for the first message of a new chat.
    cid = done_of(events)["conversation_id"]
    r = client.patch(f"/api/chat/conversations/{cid}/memory", headers=h, json={"memory_off": True})
    assert r.status_code == 200 and CONVERSATION <= set(r.json()) and r.json()["memory_off"] is True
    script(monkeypatch, reply("Fine."))
    events = stream(client, h, "Always call me Dr Asha.", cid)
    assert not of(events, "preference_noted") and not of(events, "recall")
    script(monkeypatch, reply("Noted that this chat is private."))
    first = done_of(stream(client, h, "hello there", None, memory_off=True))
    assert conversation(client, h, first["conversation_id"])["memory_off"] is True

    # Something to reset: a preference and a finished task.
    p = client.post("/api/preferences", headers=h, json={"text": "Keep answers short.", "task_type": "general"})
    assert p.status_code == 200
    # Reset (api.resetMemory) needs the word RESET.
    assert client.post("/api/memory/reset", headers=h, json={"scope": "all"}).status_code == 422
    r = client.post("/api/memory/reset", headers=h, json={"scope": "all", "confirm": "RESET"})
    assert r.status_code == 200, r.text
    deleted = r.json()["deleted"]
    assert set(deleted) == {"preferences", "work_items", "memories"} and deleted["preferences"] >= 1
    assert deleted["work_items"] >= 1
    assert client.get("/api/preferences?include_inactive=true", headers=h).json() == []
    assert client.get("/api/work-items", headers=h).json() == []
    assert client.get("/api/memories", headers=h).json() == []
    for scope in ("preferences", "task_memory", "facts"):
        r = client.post("/api/memory/reset", headers=h, json={"scope": scope, "confirm": "RESET"})
        assert r.status_code == 200 and set(r.json()["deleted"]) == {"preferences", "work_items", "memories"}


# ---------------------------------------------------------------------------
# (h) A local install: connect a folder, then a card for a path outside it
# ---------------------------------------------------------------------------


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "local_mode", True, raising=False)
    monkeypatch.setattr(settings, "device_control", "on", raising=False)
    monkeypatch.setattr(settings, "remote_access", False, raising=False)
    monkeypatch.setattr(settings, "device_allowed_roots", "", raising=False)
    monkeypatch.setattr(settings, "device_command_denylist", "", raising=False)
    monkeypatch.setattr(deps, "_generated_local_key", "", raising=False)
    reset_cache()
    access.reset_state()
    return tmp_path


def local_sign_in(client):
    saved = settings.aura_local_login_key
    settings.aura_local_login_key = "flow-test-key"
    try:
        r = client.post("/api/auth/local", json={"key": "flow-test-key"})
    finally:
        settings.aura_local_login_key = saved
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_h_local_connect_folder_and_answer_a_folder_card(client, monkeypatch, local):
    work = local / "Work"
    other = local / "Elsewhere"
    work.mkdir()
    other.mkdir()
    h = local_sign_in(client)
    status = client.get("/api/auth/config").json()
    assert status["local_mode"] is True and status["device_control"] is True
    dev = client.get("/api/device", headers=h).json()
    assert dev["enabled"] is True and dev["presence_accepted"] is True and "policy" in dev

    # Settings -> This computer: connect a folder (api.connectDeviceFolder), see it listed.
    r = client.post("/api/device/folders", headers=h, json={"path": str(work), "can_delete": False})
    assert r.status_code == 200, r.text
    folder = r.json()
    assert FOLDER <= set(folder) and folder["path"] == os.path.realpath(str(work))
    assert folder["can_delete"] is False and folder["source"] == "user"
    assert [f["id"] for f in client.get("/api/device/folders", headers=h).json()] == [folder["id"]]
    apps = client.get("/api/device/apps", headers=h).json()
    assert {"grants", "blocks", "running"} <= set(apps)

    # A write inside the folder just runs.
    inside = work / "notes.txt"
    script(monkeypatch,
           reply("", call("write_file", path=str(inside), content="hello")),
           reply("Saved notes.txt."))
    events = stream(client, h, "save a note called notes.txt in my Work folder")
    # Ask first: a write inside a connected folder is an ordinary approval card, not a folder card.
    (plain,) = pending(client, h)
    assert plain["card"] == "tool" and plain["tool_name"] == "write_file" and plain["always_user"] is False
    assert "folder_access" not in [e.get("card") for e in pending(client, h)] and not inside.exists()
    client.post(f"/api/pending-actions/{plain['id']}", headers=h, json={"decision": "once"})
    carry_on(client, h, done_of(events)["work_item_id"])
    assert inside.read_text() == "hello"

    # A path outside the connected folder asks for the folder itself.
    target = other / "plan.txt"
    script(monkeypatch,
           reply("", call("write_file", path=str(target), content="the plan")),
           reply("Saved plan.txt."))
    events = stream(client, h, "save the plan as plan.txt in Elsewhere")
    done = done_of(events)
    assert done["work_status"] == "waiting" and not target.exists()
    (card,) = pending(client, h)
    assert PENDING <= set(card)
    assert card["card"] == "folder_access" and card["always_user"] is True
    assert card["options"] == ["approve", "reject"]
    assert card["card_payload"]["path"] == os.path.realpath(str(other))
    assert card["tool_name"] == "write_file" and card["work_item_id"] == done["work_item_id"]

    # Answer it (the card offers approve/reject), then carry on.
    d = client.post(f"/api/pending-actions/{card['id']}", headers=h, json={"decision": "approve"})
    assert d.status_code == 200, d.text
    assert d.json()["decision"] == "once" and d.json()["work_item"]["status"] == "ready"
    assert target.read_text() == "the plan"
    folders = {f["path"]: f for f in client.get("/api/device/folders", headers=h).json()}
    assert os.path.realpath(str(other)) in folders
    added = folders[os.path.realpath(str(other))]
    assert FOLDER <= set(added) and added["source"] == "request" and added["can_delete"] is False
    events = carry_on(client, h, done["work_item_id"])
    assert of(events, "report")[0]["status"] == "done"
    assert pending(client, h) == []

    # Said no instead: nothing is connected and nothing is written.
    third = local / "Third"
    third.mkdir()
    script(monkeypatch,
           reply("", call("write_file", path=str(third / "x.txt"), content="x")),
           reply("I could not save it."))
    events = stream(client, h, "save x.txt in Third")
    (card,) = pending(client, h)
    assert card["card"] == "folder_access"
    d = client.post(f"/api/pending-actions/{card['id']}", headers=h, json={"decision": "reject"})
    assert d.status_code == 200 and d.json()["status"] == "rejected"
    carry_on(client, h, done_of(events)["work_item_id"])
    assert not (third / "x.txt").exists()
    assert os.path.realpath(str(third)) not in [f["path"] for f in client.get("/api/device/folders", headers=h).json()]
