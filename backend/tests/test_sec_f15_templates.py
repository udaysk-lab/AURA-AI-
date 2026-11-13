"""F15: a trigger's text (sender, subject, body) is filled into prose fields only; a templated target is refused."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402

from app import models  # noqa: E402
from app.models import utcnow  # noqa: E402
from app.automation import engine  # noqa: E402
from tests.sec_world import build  # noqa: E402

CTX = {"subject": "../../.ssh/id_rsa", "sender": "eve@evil.example", "body": "http://169.254.169.254/", "email_id": "m1"}


@pytest.mark.parametrize("tool,args,expect_args,expect_templated", [
    ("send_email", {"to": "{sender}", "subject": "Re: {subject}", "body": "{body}"},
     {"to": "{sender}", "subject": "Re: ../../.ssh/id_rsa", "body": "http://169.254.169.254/"}, ["to"]),
    ("write_file", {"path": "~/work/{subject}", "content": "{body}"},
     {"path": "~/work/{subject}", "content": "http://169.254.169.254/"}, ["path"]),
    ("fetch_url", {"url": "{body}"}, {"url": "{body}"}, ["url"]),
    ("run_command", {"command": "echo {subject}", "workdir": "{subject}"},
     {"command": "echo {subject}", "workdir": "{subject}"}, ["command", "workdir"]),
    ("create_event", {"title": "{subject}", "attendees": ["{sender}"]},
     {"title": "../../.ssh/id_rsa", "attendees": ["{sender}"]}, ["attendees"]),
    ("create_task", {"title": "{subject}", "notes": "From {sender}"},
     {"title": "../../.ssh/id_rsa", "notes": "From eve@evil.example"}, []),
    ("get_email", {"email_id": "{email_id}"}, {"email_id": "m1"}, []),
    ("open_app", {"name": "{body}"}, {"name": "{body}"}, ["name"]),
])
def test_only_text_fields_are_filled(tool, args, expect_args, expect_templated):
    out, templated = engine.interpolate_args(tool, args, CTX)
    assert out == expect_args and templated == expect_templated


def test_a_value_is_never_expanded_twice():
    out, _ = engine.interpolate_args("notify", {"title": "{subject}", "body": "x"}, {"subject": "{body}", "body": "SECRET"})
    assert out["title"] == "{body}"


def test_sender_is_the_address_not_the_display_name(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch)
    w.db.add(models.AutomationRule(user_id=w.user.id, name="r", natural_language="r", trigger_type="email_received",
                                   trigger_config={}, actions=[{"tool": "notify", "arguments": {}}], enabled=True))
    email = models.EmailMessage(user_id=w.user.id, sender="eve@evil.example",
                                sender_name="ceo@bank.example", subject="s", body="b", received_at=utcnow())
    w.db.add(email)
    w.db.commit()
    seen = []
    monkeypatch.setattr(engine, "run_rule", lambda db, user, rule, context=None: seen.append(context) or {})
    engine.on_email_received(w.db, w.user, email)
    assert seen[0]["sender"] == "eve@evil.example"


def test_a_rule_with_a_templated_target_is_refused_not_run(tmp_path, monkeypatch):
    w = build(tmp_path, monkeypatch, mode="skip")
    target = os.path.join(w.work, "victim.txt")
    rule = models.AutomationRule(
        user_id=w.user.id, name="r", natural_language="r", trigger_type="email_received", trigger_config={},
        actions=[{"tool": "create_task", "arguments": {"title": "{subject}", "notes": "{sender}"}},
                 {"tool": "write_file", "arguments": {"path": w.work + "/{subject}", "content": "x"}}],
        enabled=True, approval_mode="ask",
    )
    w.db.add(rule)
    w.db.commit()
    out = engine.run_rule(w.db, w.user, rule, {"subject": "victim.txt", "sender": "eve@evil.example"})
    assert [e["tool"] for e in out["executed"]] == ["create_task"]
    assert out["blocked"] and out["blocked"][0]["reason_code"] == "TEMPLATED_TARGET"
    assert not os.path.exists(target) and not os.path.exists(w.work + "/{subject}")
