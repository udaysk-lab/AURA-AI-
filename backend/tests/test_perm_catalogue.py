"""The tool catalogue (spec 10-permissions.md section 3): complete, consistent, fails closed."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402

from app import plugins  # noqa: E402
from app.agent import skills, tools  # noqa: E402
from app.permissions import catalogue  # noqa: E402
from app.permissions.catalogue import CATALOGUE, ToolPolicy  # noqa: E402

LOOP_TOOLS = {"aura_plan", "aura_progress", "aura_check", "aura_outcome", "aura_note_preference"}


def test_catalogue_covers_registry():
    assert set(tools.REGISTRY) == {n for n, p in CATALOGUE.items() if not p.loop_internal}
    assert catalogue.missing_from_catalogue(tools.REGISTRY) == []


def test_catalogue_system_matches_plugins():
    for name, policy in CATALOGUE.items():
        if policy.loop_internal:
            assert plugins.plugin_for_tool(name) is None
            continue
        plugin = plugins.plugin_for_tool(name)
        assert plugin is not None, name
        assert policy.system == plugin.id, name


def test_unknown_tool_has_no_policy_and_is_reported(caplog):
    assert catalogue.policy_for("format_the_disk") is None
    assert not catalogue.is_known("format_the_disk")
    with caplog.at_level("ERROR", logger="aura.permissions"):
        assert catalogue.log_missing(["read_file", "format_the_disk"]) == ["format_the_disk"]
    assert "format_the_disk" in caplog.text


def test_unlisted_tool_hidden_from_model(monkeypatch):
    from app.db import SessionLocal
    from app.models import User
    from tests.schema import drop_test_schema, ensure_test_schema
    from app.db import engine

    ensure_test_schema(engine)
    try:
        with SessionLocal() as db:
            user = User(email="cat@example.com", name="Cat")
            db.add(user)
            db.commit()
            visible = skills.enabled_tools(db, user.id)
            assert "list_tasks" in visible
            monkeypatch.delitem(CATALOGUE, "list_tasks")
            assert "list_tasks" not in skills.enabled_tools(db, user.id)
            assert "create_task" in skills.enabled_tools(db, user.id)
    finally:
        drop_test_schema(engine)


def test_bookkeeping_is_internal_workspace():
    for name in LOOP_TOOLS:
        p = CATALOGUE[name]
        assert (p.system, p.kind, p.loop_internal) == ("*", "workspace", True)
        assert name not in tools.REGISTRY
    assert catalogue.default_disposition("workspace", "ask") == "allow"


def test_no_settings_kind_without_always_user():
    assert "settings" not in catalogue.KINDS
    with pytest.raises(ValueError):
        ToolPolicy("x", "core-tasks", "settings")  # not a kind
    for p in CATALOGUE.values():
        assert p.kind in catalogue.KINDS
        if p.kind in catalogue.ALWAYS_USER_KINDS:
            assert p.always_user, p.name
    with pytest.raises(ValueError):
        ToolPolicy("x", "device-control", "file_delete")  # always-user kinds cannot opt out


def test_fetch_url_is_network_and_asks():
    p = CATALOGUE["fetch_url"]
    assert p.kind == "network"
    assert catalogue.default_disposition(p.kind, "ask") == "ask"
    assert catalogue.default_disposition(p.kind, "auto") == "reviewer"
    assert catalogue.default_disposition(p.kind, "skip") == "allow"


@pytest.mark.parametrize(
    "kind, ask, auto, skip",
    [
        ("read", "allow", "allow", "allow"),
        ("file_read", "allow", "allow", "allow"),
        ("workspace", "allow", "allow", "allow"),
        ("workspace_delete", "ask", "reviewer", "allow"),
        ("send", "ask", "reviewer", "allow"),
        ("exec", "ask", "reviewer", "allow"),
        ("screen_input", "ask", "reviewer", "allow"),
        ("file_delete", "always_user", "always_user", "always_user"),
    ],
)
def test_default_dispositions(kind, ask, auto, skip):
    assert [catalogue.default_disposition(kind, m) for m in ("ask", "auto", "skip")] == [ask, auto, skip]


def test_spec_rows():
    c = CATALOGUE
    assert c["delete_task"].kind == "workspace_delete"
    for n in ("create_task", "complete_task", "save_memory", "notify", "delegate_task", "chase_delegation", "desktop_notify"):
        assert c[n].kind == "workspace", n
    assert c["delete_file"].always_user and c["delete_file"].kind == "file_delete"
    assert c["run_command"].dyn_always_user and c["run_in_background"].dyn_always_user
    assert not c["send_process_input"].targets
    assert [t.arg for t in c["move_file"].targets] == ["source", "destination"]
    assert catalogue.is_read_only("read_file") and not catalogue.is_read_only("write_file")
    assert not catalogue.is_read_only("nonexistent")


def test_describe_matches_client_shape():
    rows = {r["tool"]: r for r in catalogue.describe()}
    assert set(rows) == set(tools.REGISTRY)
    assert rows["send_email"] == {"tool": "send_email", "system": "core-inbox", "kind": "send", "targets": ["recipient"], "always_user": False,
                               "action_text": "send an email", "note": ""}
    assert rows["fetch_url"]["targets"] == ["domain"]
    assert rows["run_command"]["targets"] == ["command"]  # the workdir is not a rule target
    assert not (LOOP_TOOLS & set(rows))


def test_every_catalogue_tool_has_both_human_phrases():
    assert set(catalogue.PHRASES) == set(CATALOGUE), "add (action, -ing) phrases for every catalogue tool"
    for name in CATALOGUE:
        action, ing = catalogue.action_phrase(name), catalogue.ing_phrase(name)
        assert action and ing, name
        assert action == action.strip() and ing == ing.strip(), name
        assert not action.lower().startswith(("aura", "wants")), name  # "AURA wants to <action>"
        assert ing.split()[0].endswith("ing"), name  # "Always allow <ing>"
    assert catalogue.action_phrase("create_task") == "create a task"
    assert catalogue.ing_phrase("create_task") == "creating tasks"
    assert catalogue.action_phrase("format_the_disk") is None and catalogue.ing_phrase("format_the_disk") is None


def test_rule_text_has_no_fallback_wording_for_catalogue_tools():
    from app.permissions import approvals, gate

    for name in CATALOGUE:
        text = approvals.rule_text(gate.RuleDraft(system="x", tool=name, applies_to="", items=()))
        assert text == f"Always allow {catalogue.ing_phrase(name)}", name
        assert "using " not in text, name


def test_pending_action_out_carries_the_action_phrase():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.schemas import PendingActionOut

    base = dict(id="a", skill_code="", arguments={}, preview="", status="pending", created_at=datetime.now(timezone.utc))
    assert PendingActionOut.model_validate(SimpleNamespace(tool_name="create_task", **base)).action_text == "create a task"
    assert PendingActionOut.model_validate(SimpleNamespace(tool_name="folder_access", **base)).action_text is None
