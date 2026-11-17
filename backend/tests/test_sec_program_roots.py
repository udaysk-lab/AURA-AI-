"""Programs started by name: folders that were ever connected count, shutdown-like programs always ask, and a link
whose host cannot be looked up is not opened."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from app.agent.coordinator import execute_tool
from app.device import apps, net, programs
from app.models import DeviceFolder, utcnow
from app.permissions import access, floors, gate
from tests.test_r2c_open_by_name import (POSIX_ONLY, assert_asks_the_owner, bound, decide, make_exe, park, start,  # noqa: F401
                                         w)


def revoke_every_folder(w):
    for row in w.db.scalars(select(DeviceFolder)).all():
        row.revoked_at = utcnow()
    w.db.commit()


# -- a folder that was connected once still counts ---------------------------------------------------------------------


@POSIX_ONLY
def test_a_program_written_into_a_folder_that_is_no_longer_connected_still_asks(w):
    entry = make_exe(w.inside, "tool")
    w.path(w.inside)
    assert_asks_the_owner(decide(w, "tool"))  # connected now
    revoke_every_folder(w)
    assert access.snapshot(w.db, w.user).roots == ()
    assert access.snapshot(w.db, w.user).program_roots
    d = decide(w, "tool")
    assert_asks_the_owner(d)
    assert d.card_payload["unchecked_open"]["path"] == entry


@POSIX_ONLY
def test_the_handler_judges_the_same_way_and_starts_nothing_unapproved(w):
    entry = make_exe(w.inside, "tool")
    w.path(w.inside)
    revoke_every_folder(w)
    with bound(w):
        out = apps.open_app("tool", program=entry, owner_approved=False)
    assert "needs your approval" in out["error"] and w.started == []


@POSIX_ONLY
def test_answering_the_card_starts_exactly_that_program(w):
    entry = make_exe(w.inside, "tool")
    w.path(w.inside)
    revoke_every_folder(w)
    start(w, "tool")
    assert [(k, a[0]) for k, a in w.started] == [("popen", entry)]


@POSIX_ONLY
def test_a_folder_that_was_never_connected_is_unchanged(w):
    make_exe(w.outside, "tool")
    d = decide(w, "tool")
    assert d.reason_code != "UNCHECKED_OPEN" or not d.always_user


def test_ever_connected_folders_are_listed_with_the_revoked_ones(w):
    before = access.snapshot(w.db, w.user)
    assert before.program_roots == before.roots and before.ever
    revoke_every_folder(w)
    after = access.snapshot(w.db, w.user)
    assert after.roots == () and after.program_roots == before.ever


def test_judge_uses_the_roots_it_is_given(tmp_path):
    entry = tmp_path / "bin" / "tool"
    entry.parent.mkdir()
    entry.write_text("x")
    assert programs.judge(str(entry), (str(tmp_path),), floors.in_self_path, programs.LINUX).why == "folder"
    assert programs.judge(str(entry), (), floors.in_self_path, programs.LINUX).kind == "ok"


# -- shutdown-like programs --------------------------------------------------------------------------------------------


@POSIX_ONLY
@pytest.mark.parametrize("name", sorted(programs.SHUTDOWN_NAMES))
def test_a_program_that_ends_the_session_asks_wherever_it_is(w, name):
    entry = make_exe(w.outside, name)  # outside every connected folder
    d = decide(w, name)
    assert_asks_the_owner(d, "shut down, restart, suspend or sign out")
    assert d.card_payload["unchecked_open"]["path"] == entry
    assert d.card_payload["unchecked_open"]["why"].startswith("It would shut down")
    assert d.always_user and tuple(d.options) == ("once", "reject")


@pytest.mark.parametrize("name,platform", [("shutdown.exe", programs.WINDOWS), ("LogOff.EXE", programs.WINDOWS),
                                           ("poweroff", programs.LINUX), ("tsdiscon.exe", programs.WINDOWS)])
def test_the_judgement_ignores_case_and_program_suffixes(tmp_path, name, platform):
    entry = tmp_path / name
    entry.write_text("x")
    j = programs.judge(str(entry), (), floors.in_self_path, platform)
    assert (j.kind, j.why) == ("owner", "shutdown")


@pytest.mark.parametrize("name", ["shutdowns", "my-reboot-tool", "haltingstate", "suspend2", "poweroff.txt"])
def test_other_names_are_not_caught(tmp_path, name):
    entry = tmp_path / name
    entry.write_text("x")
    assert programs.judge(str(entry), (), floors.in_self_path, programs.LINUX).kind == "ok"


@POSIX_ONLY
def test_no_mode_rule_or_reviewer_starts_a_shutdown_program_unasked(w):
    from app.permissions import rules

    make_exe(w.outside, "poweroff")
    rules.add(w.db, w.user, effect="allow", system="device-control", tool="open_app", actor="user")
    w.db.commit()
    assert_asks_the_owner(decide(w, "poweroff"))
    assert w.started == []
    w.reviewer.assert_not_called()


@POSIX_ONLY
def test_answering_the_card_starts_the_shutdown_program_only_for_that_entry(w):
    entry = make_exe(w.outside, "reboot")
    start(w, "reboot")
    assert [(k, a[0]) for k, a in w.started] == [("popen", entry)]


@POSIX_ONLY
def test_the_handler_does_not_start_one_that_was_not_approved(w):
    entry = make_exe(w.outside, "halt")
    with bound(w):
        out = apps.open_app("halt", program=entry, owner_approved=False)
    assert "needs your approval" in out["error"] and w.started == []


# -- a link whose host cannot be looked up -----------------------------------------------------------------------------


def _no_dns(monkeypatch):
    def fail(host, port):
        raise OSError("no such host")

    monkeypatch.setattr(net, "resolve", fail)


def test_open_app_with_a_host_that_cannot_be_looked_up_is_refused(w, monkeypatch):
    _no_dns(monkeypatch)
    opened = []
    monkeypatch.setattr(apps, "_open_target", lambda target, label: opened.append(target) or {"message": label})
    d = decide(w, "https://does-not-exist.invalid/page")
    assert (d.kind, d.reason_code) == ("deny", "FLOOR_UNRESOLVED")
    assert "look up" in d.reason and opened == []


def test_a_host_that_resolves_is_judged_as_before(w, monkeypatch):
    monkeypatch.setattr(net, "resolve", lambda host, port: ["93.184.216.34"])
    d = decide(w, "https://example.com/page")
    assert d.kind in ("allow", "ask") and d.reason_code != "FLOOR_UNRESOLVED"


def test_a_fetch_still_leaves_the_lookup_to_the_fetch_itself(w, monkeypatch):
    _no_dns(monkeypatch)
    d = gate.decide(w.db, w.user, "fetch_url", {"url": "https://does-not-exist.invalid/x"},
                    gate.build_context(w.db, w.user, "chat"))
    assert d.reason_code != "FLOOR_UNRESOLVED"
