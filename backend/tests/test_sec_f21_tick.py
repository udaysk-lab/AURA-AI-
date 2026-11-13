"""F21: the tick route runs the caller's due automations only; the worker's pass still runs everyone's."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

from datetime import datetime, timezone

from app import models
from app.api import assistant
from app.automation import engine
from tests.perm_helpers import make_session, make_user


def test_tick_runs_only_the_callers_rules(tmp_path, monkeypatch):
    eng, db = make_session(tmp_path)
    alice = make_user(db, email="alice@example.com")
    bob = make_user(db, email="bob@example.com")
    for u in (alice, bob):
        db.add(models.AutomationRule(
            user_id=u.id, name=f"rule-{u.id}", natural_language="every minute", trigger_type="schedule",
            trigger_config={"cron": "* * * * *"}, actions=[{"tool": "notify", "arguments": {"title": "x"}}],
            enabled=True, created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        ))
    db.commit()
    ran = []
    monkeypatch.setattr(engine, "run_rule", lambda db, user, rule, context=None: ran.append(user.id) or {"rule": rule.name})
    out = assistant.tick(alice, db)
    assert ran == [alice.id] and out["fired"] >= 1
    ran.clear()
    engine.run_due_schedules(db)  # the worker and the cron route
    assert sorted(ran) == sorted([alice.id, bob.id])
    db.close()
    eng.dispose()
