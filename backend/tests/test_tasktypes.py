"""Task typing (spec 20 section 4): keyword classifier, tool majority, normaliser."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402

from app.services import tasktypes  # noqa: E402


@pytest.mark.parametrize("sentence, expected", [
    ("Reply to Jane's email about the invoice", "email"),
    ("Check my inbox for anything unread", "email"),
    ("What's on my calendar tomorrow?", "scheduling"),
    ("Find free time for a meeting with Sam on Friday", "scheduling"),
    ("Add a task to call Priya about the Q3 report", "todo"),
    ("Remind me to renew the passport", "todo"),
    ("Delegate the slide deck to Omar and chase him on Thursday", "delegation"),
    ("Research flight options to Lisbon", "research"),
    ("Draft a blog post about our launch", "writing"),
    ("Summarise this pdf for me", "documents"),
    ("Rename the invoices folder", "files"),
    ("Run the build script", "commands"),
    ("Open the app Slack and click the search box", "computer"),
    ("Catch me up on my day", "briefing"),
    ("Who is Priya?", "contacts"),
    ("Thanks, that is all", "other"),
    ("", "other"),
])
def test_classify_examples(sentence, expected):
    assert tasktypes.classify(sentence) == expected


def test_first_matching_rule_wins():
    # "run" (commands) is checked before "email"; "file" before "email"
    assert tasktypes.classify("run a script that sends the email") == "commands"
    assert tasktypes.classify("email me the file") == "files"


def test_words_take_plain_endings_but_do_not_run_on():
    assert tasktypes.classify("check my emails") == "email"
    assert tasktypes.classify("I booked a table") == "scheduling"
    assert tasktypes.classify("the postal code") == "other"  # "post" does not match inside "postal"
    assert tasktypes.classify("report.pdf is attached") == "files"  # the extension rule


@pytest.mark.parametrize("raw, expected", [
    ("email", "email"),
    ("  Email ", "email"),
    ("Calendar", "scheduling"),
    ("to-do", "todo"),
    ("To Do", "todo"),
    ("tasks", "todo"),
    ("Files & folders", "other"),  # not a clean token: unknown
    ("files-and-folders", "files"),
    ("apps and screen", "computer"),
    ("general", "other"),  # only preferences may be general
    ("nonsense", "other"),
    ("", "other"),
    (None, "other"),
])
def test_normalise_aliases_and_unknown(raw, expected):
    assert tasktypes.normalise(raw) == expected


def test_normalise_keeps_general_for_preferences():
    assert tasktypes.normalise("General", allow_general=True) == "general"
    assert tasktypes.normalise("email", allow_general=True) == "email"
    assert tasktypes.normalise("zzz", allow_general=True) == "other"


def test_from_tools_majority():
    assert tasktypes.from_tools(["get_email", "draft_reply", "create_task"]) == "email"
    assert tasktypes.from_tools([]) == "other"
    assert tasktypes.from_tools(["save_memory", "notify", "fetch_url"]) == "other"  # untyped tools
    # a tie goes to the type used first
    assert tasktypes.from_tools(["create_task", "send_email"]) == "todo"
    assert tasktypes.from_tools(["send_email", "create_task"]) == "email"
    assert tasktypes.from_tools(["save_memory", "create_event", "create_event", "send_email"]) == "scheduling"


def test_taxonomy_shape():
    assert list(tasktypes.TYPES)[-1] == "other" and "general" not in tasktypes.TYPES
    assert len(tasktypes.TYPES) == 13
    assert tasktypes.options()[0] == {"value": "email", "label": "Email"}
    assert set(tasktypes.TOOL_TYPES.values()) <= set(tasktypes.TYPES)


def test_every_typed_tool_exists():
    from app.permissions import catalogue

    assert set(tasktypes.TOOL_TYPES) <= set(catalogue.CATALOGUE)


def test_skill_code_type():
    assert tasktypes.from_skill_code("EM01") == "email"
    assert tasktypes.from_skill_code("dv02") == "files"
    assert tasktypes.from_skill_code("MM01") == "general"  # first tool is untyped
    assert tasktypes.from_skill_code("NOPE") == "general"
