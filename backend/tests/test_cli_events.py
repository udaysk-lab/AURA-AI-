"""The terminal client reads the task-loop events (spec 20 sections 8 and 14, WP7 backend part)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import httpx  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aura_cli  # noqa: E402

WI = "wi-1"
PA = "pa-1"


def sse(*events) -> str:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    return body + "data: [DONE]\n\n"


FIRST = sse(
    {"type": "start", "conversation_id": "c-1"},
    {"type": "status", "value": "thinking"},
    {"type": "recall", "work_items": 1, "preferences": 2, "memories": 0},
    {"type": "preference_noted", "id": "p1", "text": "Keep emails short", "task_type": "email",
     "superseded_id": None, "work_item_id": None},
    {"type": "task", "work_item_id": WI, "title": "Send Priya the numbers", "task_type": "email",
     "status": "running", "resumed": False},
    {"type": "plan", "work_item_id": WI, "steps": [
        {"index": 0, "text": "Check tasks", "status": "todo", "note": ""},
        {"index": 1, "text": "Send the email", "status": "todo", "note": ""}]},
    {"type": "step_started", "work_item_id": WI, "step_no": 1, "seq": 1, "tool": "list_tasks", "system": "",
     "label": "Looking at your tasks"},
    {"type": "step_finished", "work_item_id": WI, "step_no": 1, "seq": 1, "tool": "list_tasks", "ok": True,
     "summary": "3 tasks", "code": "TK01"},
    {"type": "reviewer_block", "work_item_id": WI, "tool": "delete_file", "reason": "Not asked for", "streak": 1,
     "total": 1},
    {"type": "approval_needed", "work_item_id": WI, "pending_action_id": PA, "tool_name": "send_email",
     "skill_code": "EM02", "preview": "Send an email to priya@example.com", "expires_at": "2026-11-13T10:00:00+00:00"},
    {"type": "waiting", "work_item_id": WI, "pending_action_ids": [PA], "expires_at": None},
    {"type": "delta", "value": "I need your go-ahead."},
    {"type": "done", "conversation_id": "c-1", "message_id": "m1", "latency_ms": 5, "steps": 2,
     "work_item_id": WI, "work_status": "waiting"},
)

SECOND = sse(
    {"type": "start", "conversation_id": "c-1"},
    {"type": "task", "work_item_id": WI, "title": "Send Priya the numbers", "task_type": "email",
     "status": "running", "resumed": True},
    {"type": "verification", "work_item_id": WI, "passed": True, "method": "looked in Sent", "evidence": "there"},
    {"type": "report", "work_item_id": WI, "status": "done", "title": "Send Priya the numbers", "outcome": "Sent.",
     "verification": "passed"},
    {"type": "delta", "value": "Sent the numbers to Priya."},
    {"type": "done", "conversation_id": "c-1", "message_id": "m2", "latency_ms": 5, "steps": 1,
     "work_item_id": WI, "work_status": "done"},
)


class Server:
    """A recorded AURA: replays canned streams and records every request."""

    def __init__(self, streams, decision=None, card=None):
        self.streams = list(streams)
        self.calls: list[tuple[str, str, dict | None]] = []
        self.decision = decision or {"status": "approved", "message": "Sent", "work_item": {"id": WI, "status": "ready"}}
        self.card = card or {"id": PA, "preview": "Send an email to priya@example.com", "card": "tool",
                             "options": ["once", "window", "always", "reject"], "always_user": False,
                             "status": "pending"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if path in ("/api/chat/stream", "/api/chat/continue"):
            return httpx.Response(200, text=self.streams.pop(0), headers={"content-type": "text/event-stream"})
        if path == f"/api/pending-actions/{PA}" and request.method == "GET":
            return httpx.Response(200, json=self.card)
        if path == f"/api/pending-actions/{PA}" and request.method == "POST":
            return httpx.Response(200, json=self.decision)
        if path == "/api/chat/stop":
            return httpx.Response(200, json={"id": body["work_item_id"], "status": "stopped"})
        return httpx.Response(404, json={"detail": "no"})

    def paths(self):
        return [(m, p) for m, p, _ in self.calls]


def make(server: Server) -> aura_cli.Aura:
    aura = aura_cli.Aura("http://aura.test", client=httpx.Client(transport=httpx.MockTransport(server)))
    aura.token = "t"
    return aura


@pytest.fixture
def answers(monkeypatch):
    given = []

    def feed(*texts):
        it = iter(texts)

        def fake(prompt=""):
            given.append(prompt)
            return next(it)

        monkeypatch.setattr("builtins.input", fake)
        return given

    return feed


def test_events_are_shown_and_approvals_asked_only_after_done(answers, capsys):
    server = Server([FIRST, SECOND])
    answers("y")
    convo = make(server).ask("email Priya the numbers", None)
    out = capsys.readouterr().out
    assert convo == "c-1"
    # progress: recall, noted preference, task, plan, step, block, card, report, reply
    for needle in ("recalled 1 past tasks, 2 preferences", "noted: Keep emails short", "Send Priya the numbers",
                   "○ Check tasks", "○ Send the email", "Looking at your tasks", "[TK01] 3 tasks",
                   "blocked by the safety reviewer: delete_file", "Not asked for", "waiting for 1 approval",
                   "carrying on: Send Priya the numbers", "checked: looked in Sent (there)", "done",
                   "Sent the numbers to Priya."):
        assert needle in out, needle
    # the card is printed after the stream ended, not inside it
    assert out.index("I need your go-ahead.") < out.index("Needs your say-so")
    assert "Send an email to priya@example.com" in out and "[y] once  [n] no" in out
    # the order of requests: stream, read the card, answer it, continue the same task
    assert server.paths() == [
        ("POST", "/api/chat/stream"), ("GET", f"/api/pending-actions/{PA}"),
        ("POST", f"/api/pending-actions/{PA}"), ("POST", "/api/chat/continue"),
    ]
    assert server.calls[0][2] == {"message": "email Priya the numbers", "conversation_id": None}
    assert server.calls[2][2] == {"decision": "once"}
    assert server.calls[3][2] == {"work_item_id": WI}


def test_declining_answers_reject_and_still_continues(answers, capsys):
    server = Server([FIRST, SECOND])
    answers("n")
    make(server).ask("email Priya", None)
    assert server.calls[2][2] == {"decision": "reject"}
    assert ("POST", "/api/chat/continue") in server.paths()  # the model is told the user said no
    assert "turned down" in capsys.readouterr().out


def test_cards_only_the_app_may_answer_are_not_offered(answers, capsys):
    card = {"id": PA, "preview": "Connect the Documents folder", "card": "folder_access",
            "options": ["approve", "reject"], "always_user": True, "status": "pending"}
    server = Server([FIRST], card=card)
    given = answers("y")  # a yes is not an answer here: it is left waiting
    make(server).ask("open my documents", None)
    out = capsys.readouterr().out
    assert "needs you in the AURA app" in out and "left waiting" in out
    assert given and ("POST", f"/api/pending-actions/{PA}") not in server.paths()
    assert ("POST", "/api/chat/continue") not in server.paths()


def test_a_step_budget_continue_event_carries_on_without_asking(answers, capsys):
    paused = sse(
        {"type": "start", "conversation_id": "c-1"},
        {"type": "task", "work_item_id": WI, "title": "Long job", "task_type": "other", "status": "running",
         "resumed": False},
        {"type": "continue", "work_item_id": WI, "reason": "segment_budget"},
        {"type": "delta", "value": "Still working."},
        {"type": "done", "conversation_id": "c-1", "message_id": "m1", "latency_ms": 1, "steps": 8,
         "work_item_id": WI, "work_status": "ready"},
    )
    server = Server([paused, SECOND])
    given = answers()
    make(server).ask("do the long job", None)
    assert server.paths() == [("POST", "/api/chat/stream"), ("POST", "/api/chat/continue")]
    assert given == []  # nobody was asked anything
    assert "carrying on" in capsys.readouterr().out


def test_several_cards_in_one_step_continue_once_the_last_is_answered(answers):
    two = FIRST.replace("data: [DONE]", "data: " + json.dumps({
        "type": "approval_needed", "work_item_id": WI, "pending_action_id": PA, "tool_name": "send_email",
        "skill_code": "EM02", "preview": "Second", "expires_at": None}) + "\n\ndata: [DONE]")
    server = Server([two, SECOND])
    server.decision = {"status": "approved", "work_item": {"id": WI, "status": "waiting"}}
    calls = iter([server.decision, {"status": "approved", "work_item": {"id": WI, "status": "ready"}}])
    original = server.__call__

    def handler(request):
        if request.method == "POST" and request.url.path == f"/api/pending-actions/{PA}":
            server.calls.append((request.method, request.url.path, json.loads(request.content)))
            return httpx.Response(200, json=next(calls))
        return original(request)

    aura = aura_cli.Aura("http://aura.test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    aura.token = "t"
    answers("y", "y")
    aura.ask("send both", None)
    paths = server.paths()
    assert paths.count(("POST", f"/api/pending-actions/{PA}")) == 2
    assert paths.count(("POST", "/api/chat/continue")) == 1


def test_ctrl_c_during_a_stream_stops_the_task(capsys):
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield (b'data: {"type": "start", "conversation_id": "c-1"}\n\n'
                   b'data: {"type": "task", "work_item_id": "wi-1", "title": "T", "task_type": "other", '
                   b'"status": "running", "resumed": false}\n\n')
            raise KeyboardInterrupt

    server = Server([])

    def handler(request):
        if request.url.path == "/api/chat/stream":
            server.calls.append((request.method, request.url.path, json.loads(request.content)))
            return httpx.Response(200, stream=Interrupted(), headers={"content-type": "text/event-stream"})
        return server(request)

    aura = aura_cli.Aura("http://aura.test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    aura.token = "t"
    assert aura.ask("do something long", None) == "c-1"
    assert ("POST", "/api/chat/stop", {"work_item_id": WI}) in server.calls
    assert "stop requested" in capsys.readouterr().out
    assert ("POST", "/api/chat/continue") not in server.paths()


def test_a_409_names_the_running_task_and_makes_no_further_calls(capsys):
    def handler(request):
        return httpx.Response(409, json={"detail": "AURA is still working on this conversation", "code": "still_working",
                                         "work_item_id": WI, "status": "running"})

    aura = aura_cli.Aura("http://aura.test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    aura.token = "t"
    aura.ask("hello", "c-1")
    assert "still working" in capsys.readouterr().err


def test_old_event_names_are_not_read():
    """The removed events (skill, learned, pending_action) are not part of the protocol any more."""
    turn = aura_cli.Turn()
    aura = make(Server([]))
    aura.handle({"type": "pending_action", "id": PA, "preview": "x"}, turn)
    aura.handle({"type": "skill", "code": "EM01", "summary": "x"}, turn)
    assert turn.approvals == []


def test_status_reads_the_mode_and_connected_folders_not_the_old_policy_fields(capsys):
    """`aura status` shows the owner's permission mode and connected folders (per-user rows), and
    the install's own limit; it no longer reads policy.exec_mode."""
    device = {"enabled": True, "os": "Linux", "arch": "x86_64", "hostname": "box", "user": "me",
              "shell": {"kind": "bash"}, "shell_control": True, "file_control": True, "packages": {},
              "policy": {"allowed_roots": ["/home/me"], "protected_paths": [], "denylist_active": False},
              "active_skills": []}

    def handler(request: httpx.Request) -> httpx.Response:
        return {
            "/api/device": httpx.Response(200, json=device),
            "/api/permissions": httpx.Response(200, json={"mode": "ask", "mode_label": "Ask first"}),
            "/api/device/folders": httpx.Response(200, json=[{"id": "f1", "path": "/home/me/Work"}]),
        }.get(request.url.path, httpx.Response(404, json={"detail": "no"}))

    aura = aura_cli.Aura("http://aura.test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    aura.token = "t"
    assert aura.status() == 0
    out = capsys.readouterr().out
    assert "permission mode: Ask first" in out and "connected folders: /home/me/Work" in out
    assert "/home/me" in out and "shell approval" not in out
