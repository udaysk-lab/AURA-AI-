"""R2c: `open_app` with a bare application name starts exactly the program AURA resolved and judged.

A name that resolves to a program inside a folder AURA can change (or that cannot be found on Windows and macOS)
needs the owner every time, in every mode. The handler starts exactly the entry that was judged. Also: the protected
places that decide what opening a file runs, and the removal of the old exec-mode settings at run time."""

from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

from app import models
from app.agent.coordinator import execute_tool
from app.config import ALWAYS_PROTECTED, settings
from app.device import apps, files, programs
from app.device.runtime import DeviceUnavailable
from app.llm import ToolCall
from app.permissions import access, approvals, gate, reviewer, rules
from tests.sec_world import build

MODES = ("skip", "auto", "ask")
POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="executable bits and symbolic links are POSIX notions")


# -- helpers ---------------------------------------------------------------------------------------------------------


def make_exe(folder, name="tool", content=b"#!/bin/sh\necho hi\n"):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(str(folder), name)
    with open(path, "wb") as fh:
        fh.write(content)
    os.chmod(path, 0o755)
    return path


@pytest.fixture(params=MODES)
def w(request, tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode=request.param)
    world.mode = request.param
    verdict = mock.Mock(return_value=reviewer.ReviewerVerdict(True, "fine", ""))
    monkeypatch.setattr(reviewer, "available", lambda: True)
    monkeypatch.setattr(reviewer, "review", verdict)
    world.reviewer = verdict
    world.outside = str(tmp_path / "outside-bin")  # not inside the connected home folder
    world.inside = os.path.join(world.work, "bin")  # inside it
    os.makedirs(world.outside)
    os.makedirs(world.inside)
    world.started = []
    monkeypatch.setattr(programs, "current_platform", lambda: programs.LINUX)

    def popen(args, *a, **kw):
        world.started.append(("popen", list(args)))
        return mock.Mock()

    def run(args, *a, **kw):
        world.started.append(("run", list(args)))
        return mock.Mock(returncode=0)

    monkeypatch.setattr(apps.subprocess, "Popen", popen)
    monkeypatch.setattr(apps.subprocess, "run", run)
    monkeypatch.setattr(os, "startfile", lambda target: world.started.append(("startfile", target)), raising=False)
    world.path = lambda *folders: monkeypatch.setenv("PATH", os.pathsep.join(str(f) for f in folders))
    world.path(world.outside)
    yield world
    world.db.close()
    world.engine.dispose()


def decide(w, name, trigger="chat"):
    return gate.decide(w.db, w.user, "open_app", {"name": name}, gate.build_context(w.db, w.user, trigger))


def card(d):
    return d.card_payload["unchecked_open"]


def assert_asks_the_owner(d, why=None):
    assert d.kind == "ask" and d.reason_code == "UNCHECKED_OPEN", (d.kind, d.reason_code, d.reason)
    assert d.source == "always_user" and d.always_user and d.card == "tool"
    assert tuple(d.options) == ("once", "reject") and d.proposed_rule is None
    assert set(card(d)) == {"name", "path", "type", "why"}
    assert "your account" in d.reason and "once only" in d.reason
    if why:
        assert why in d.reason


def park(w, name):
    ctx = gate.build_context(w.db, w.user, "chat")
    args = {"name": name}
    d = gate.decide(w.db, w.user, "open_app", args, ctx)
    assert d.kind == "ask", (d.kind, d.reason_code)
    return approvals.queue_action(w.db, w.user, ctx, ToolCall(id="c1", name="open_app", arguments=args), d)


def start(w, name):
    """The way a call really goes: decide, then run if allowed, or park and answer once. Returns the decision."""
    d = decide(w, name)
    if d.kind == "allow":
        execute_tool(w.db, w.user, "open_app", {"name": name}, decision=d)
    elif d.kind == "ask":
        out = approvals.resolve(w.db, w.user, park(w, name).id, "once")
        assert out["status"] == "approved", out
    return d


def switch(monkeypatch, platform, *, registry=(), roots=(), which=None):
    monkeypatch.setattr(programs, "current_platform", lambda: platform)
    monkeypatch.setattr(programs, "_registry_values", lambda name: list(registry))
    monkeypatch.setattr(programs, "APPLICATION_ROOTS", tuple(str(r) for r in roots))
    if which is not None:
        monkeypatch.setattr(programs.shutil, "which", which)


def bound(w):
    return access.use(access.snapshot(w.db, w.user))


# -- a name outside every connected folder follows the mode ---------------------------------------------------------------


@POSIX_ONLY
def test_a_name_outside_the_connected_folders_follows_the_mode_and_starts_that_entry(w):
    entry = make_exe(w.outside)
    d = decide(w, "tool")
    if w.mode == "skip":
        assert (d.kind, d.reason_code) == ("allow", "MODE_SKIP")
    elif w.mode == "ask":
        assert (d.kind, d.reason_code) == ("ask", "MODE_ASK") and not d.always_user
    else:
        assert (d.kind, d.reason_code) == ("allow", "REVIEWER_ALLOW")
        w.reviewer.assert_called()
    start(w, "tool")
    assert [(k, a[0]) for k, a in w.started] == [("popen", entry)] and w.started[0][1] == [entry]


# -- an entry inside a connected folder asks, always ----------------------------------------------------------------------


@POSIX_ONLY
def test_an_entry_inside_a_connected_folder_asks_in_every_mode(w):
    entry = make_exe(w.inside)
    w.path(w.inside)
    d = decide(w, "tool")
    assert_asks_the_owner(d, "folder AURA can change")
    assert card(d) == {"name": "tool", "path": entry, "type": "",
                       "why": "It is in a folder AURA can change."}
    w.reviewer.assert_not_called()
    assert not w.started


@POSIX_ONLY
def test_no_rule_window_or_reviewer_covers_a_program_inside_a_connected_folder(w):
    from datetime import timedelta

    from app.models import utcnow

    make_exe(w.inside)
    w.path(w.inside)
    for kwargs in (
        dict(tool="open_app", target_kind="app", pattern="tool"),
        dict(tool=None, target_kind="", pattern=""),
        dict(tool="open_app", target_kind="", pattern=""),
        dict(tool="open_app", target_kind="app", pattern="tool", expires_at=utcnow() + timedelta(minutes=10)),
    ):
        rules.add(w.db, w.user, effect="allow", system="device-control", actor="user", **kwargs)
        w.db.commit()
        assert_asks_the_owner(decide(w, "tool"))
    w.reviewer.assert_not_called()


@POSIX_ONLY
def test_a_link_inside_a_connected_folder_that_leads_outside_asks(w):
    target = make_exe(w.outside, "real-tool")
    os.makedirs(w.inside, exist_ok=True)
    link = os.path.join(w.inside, "tool")
    os.symlink(target, link)
    w.path(w.inside)
    d = decide(w, "tool")
    assert_asks_the_owner(d)
    assert card(d)["path"] == link  # the entry as found, links not followed


@POSIX_ONLY
def test_an_entry_outside_that_is_a_link_into_a_connected_folder_asks(w):
    real = make_exe(w.inside, "real-tool")
    link = os.path.join(w.outside, "tool")
    os.symlink(real, link)
    d = decide(w, "tool")
    assert_asks_the_owner(d)
    assert card(d)["path"] == link
    if w.mode != "ask":
        w.reviewer.assert_not_called()


@POSIX_ONLY
def test_overwriting_an_existing_executable_keeps_it_startable_and_the_gate_still_asks(w):
    """The end-to-end reason for this package: write_text opens an existing file with "w", which keeps its mode."""
    entry = make_exe(w.inside, "tool", b"#!/bin/sh\necho harmless\n")
    w.path(w.inside)
    with bound(w):
        files.write_text(entry, "#!/bin/sh\necho something else\n")
    assert os.access(entry, os.X_OK)
    assert_asks_the_owner(decide(w, "tool"))
    w.reviewer.assert_not_called()
    assert not w.started


# -- nothing found --------------------------------------------------------------------------------------------------------


def test_an_unresolved_name_on_linux_asks_no_question_and_starts_nothing(w):
    d = decide(w, "no-such-program")
    assert d.reason_code != "UNCHECKED_OPEN" and not d.always_user
    if d.kind == "allow":
        out = execute_tool(w.db, w.user, "open_app", {"name": "no-such-program"}, decision=d)
        assert "Couldn't find an app called" in out["error"]
    with bound(w):
        assert "Couldn't find" in apps.open_app("no-such-program", program="")["error"]
    assert not w.started


@pytest.mark.parametrize("platform", [programs.WINDOWS, programs.MACOS])
def test_an_unresolved_name_on_windows_and_macos_asks_and_after_approval_the_system_looks_it_up(w, monkeypatch, platform):
    switch(monkeypatch, platform, roots=(w.outside,))
    d = decide(w, "ghost")
    assert_asks_the_owner(d, "could not find where this application is installed")
    assert card(d) == {"name": "ghost", "path": "", "type": "", "why": "AURA could not find where it is installed."}
    w.reviewer.assert_not_called()
    out = approvals.resolve(w.db, w.user, park(w, "ghost").id, "once")
    assert out["status"] == "approved", out
    if platform == programs.WINDOWS:
        assert w.started == [("startfile", "ghost")]
    else:
        assert w.started == [("run", ["open", "-a", "ghost"])]


def test_windows_starts_exactly_the_entry_that_was_judged(w, monkeypatch):
    script = make_exe(w.inside, "notes.cmd", b"echo hi\n")
    switch(monkeypatch, programs.WINDOWS, which=lambda n: script if n == "notes" else None)
    assert_asks_the_owner(decide(w, "notes"))
    approvals.resolve(w.db, w.user, park(w, "notes").id, "once")
    assert w.started == [("startfile", script)]


def test_macos_starts_exactly_the_bundle_that_was_judged(w, monkeypatch):
    apps_dir = os.path.join(w.home, "Applications")
    os.makedirs(os.path.join(apps_dir, "Thing.app"))
    switch(monkeypatch, programs.MACOS, roots=(apps_dir,))
    d = decide(w, "thing")
    assert_asks_the_owner(d)
    assert card(d)["path"] == os.path.join(apps_dir, "Thing.app") and card(d)["type"] == ".app"
    approvals.resolve(w.db, w.user, park(w, "thing").id, "once")
    assert w.started == [("run", ["open", "-a", os.path.join(apps_dir, "Thing.app")])]


def test_a_macos_bundle_outside_the_connected_folders_follows_the_mode(w, monkeypatch):
    apps_dir = os.path.join(os.path.dirname(w.home), "Apps")
    os.makedirs(os.path.join(apps_dir, "Thing.app"))
    switch(monkeypatch, programs.MACOS, roots=(apps_dir,))
    d = decide(w, "Thing")
    assert d.reason_code in ("MODE_SKIP", "MODE_ASK", "REVIEWER_ALLOW") and not d.always_user
    start(w, "Thing")
    assert w.started == [("run", ["open", "-a", os.path.join(apps_dir, "Thing.app")])]


# -- an approval covers exactly what the card showed ----------------------------------------------------------------------


@POSIX_ONLY
def test_the_approval_covers_exactly_the_entry_the_card_showed(w):
    first = make_exe(os.path.join(w.work, "a"), "tool")
    make_exe(os.path.join(w.work, "b"), "tool")
    w.path(os.path.join(w.work, "a"))
    stale = park(w, "tool")
    w.path(os.path.join(w.work, "b"))  # PATH changed between the card and the answer
    out = approvals.resolve(w.db, w.user, stale.id, "once")
    assert out["status"] == "refused" and not w.started
    w.path(os.path.join(w.work, "a"))
    out = approvals.resolve(w.db, w.user, park(w, "tool").id, "once")
    assert out["status"] == "approved" and w.started == [("popen", [first])]


@POSIX_ONLY
def test_an_approval_does_not_stretch_to_a_different_program_outside_the_folders(w):
    make_exe(os.path.join(w.work, "a"), "tool")
    make_exe(w.outside, "tool")
    w.path(os.path.join(w.work, "a"))
    action = park(w, "tool")
    w.path(w.outside)
    assert approvals.resolve(w.db, w.user, action.id, "once")["status"] == "refused"
    assert not w.started


@POSIX_ONLY
def test_a_call_parked_as_an_ordinary_ask_that_now_needs_the_owner_is_refused(w):
    if w.mode != "ask":
        pytest.skip("only Ask first parks an ordinary question for a program outside the folders")
    make_exe(w.outside, "tool")
    action = park(w, "tool")
    assert action.reason_code == "MODE_ASK"
    make_exe(w.inside, "tool")
    w.path(w.inside)
    assert approvals.resolve(w.db, w.user, action.id, "once")["status"] == "refused"
    assert not w.started


def test_an_unfound_name_that_is_found_after_the_card_is_refused(w, monkeypatch):
    switch(monkeypatch, programs.WINDOWS)
    action = park(w, "ghost")
    found = make_exe(w.outside, "ghost.cmd", b"echo hi\n")
    monkeypatch.setattr(programs.shutil, "which", lambda n: found)
    assert approvals.resolve(w.db, w.user, action.id, "once")["status"] == "refused"
    assert not w.started


# -- the handler judges again ---------------------------------------------------------------------------------------------


@POSIX_ONLY
def test_the_handler_refuses_when_its_own_resolution_differs_from_the_judged_entry(w):
    one = make_exe(w.outside, "tool")
    other = make_exe(os.path.join(w.outside, "more"), "tool")
    with bound(w):
        out = apps.open_app("tool", program=other, owner_approved=True)
        assert "no longer leads" in out["error"]
        os.remove(one)
        assert "no longer leads" in apps.open_app("tool", program=other)["error"]
    assert not w.started


@POSIX_ONLY
def test_the_handler_refuses_a_program_that_needs_the_owner_unless_the_owner_approved_it(w):
    entry = make_exe(w.inside, "tool")
    w.path(w.inside)
    with bound(w):
        assert "needs your approval" in apps.open_app("tool", program=entry)["error"]
        assert not w.started
        assert "error" not in apps.open_app("tool", program=entry, owner_approved=True)
    assert w.started == [("popen", [entry])]


@pytest.mark.parametrize("platform", [programs.LINUX, programs.WINDOWS, programs.MACOS])
def test_a_call_without_a_judgement_starts_nothing(w, monkeypatch, platform, tmp_path):
    """Nothing judged means nothing starts: a name is treated as one the gate found nothing for."""
    script = make_exe(w.outside, "tool.cmd", b"echo hi\n")
    bundle = tmp_path / "Apps" / "Tool.app"
    bundle.mkdir(parents=True)
    switch(monkeypatch, platform, roots=(bundle.parent,),
           which=lambda n: script if n in ("tool", "tool.cmd") else None)
    if platform == programs.LINUX:
        make_exe(w.outside, "tool")
    with bound(w):
        out = apps.open_app("tool")  # it resolves to something now, which nobody judged
        assert "error" in out
        out = apps.open_app("ghost-name")  # nothing found, nothing approved
        assert "error" in out
        assert "error" in apps.open_app("tool", program=None, owner_approved=True)
    assert not w.started


@POSIX_ONLY
def test_the_gate_decision_is_what_the_agent_loop_hands_the_handler(w):
    if w.mode == "ask":
        pytest.skip("Ask first parks the call; covered by the approval tests")
    entry = make_exe(w.outside, "tool")
    d = decide(w, "tool")
    assert [t.program for t in d.targets if t.kind == "app"] == [entry]
    make_exe(os.path.join(w.outside, "later"), "tool")
    w.path(os.path.join(w.outside, "later"), w.outside)  # a different entry answers now
    out = execute_tool(w.db, w.user, "open_app", {"name": "tool"}, decision=d)
    assert "error" in out and not w.started


@POSIX_ONLY
def test_a_path_folder_spelled_through_a_link_into_a_connected_folder_is_judged_by_its_true_place(w):
    """The entry is itself a link that leads outside, but it lives in a folder AURA can change."""
    outside_target = make_exe(w.outside, "real-tool")
    os.makedirs(w.inside, exist_ok=True)
    os.symlink(outside_target, os.path.join(w.inside, "tool"))
    spelled = os.path.join(w.outside, "link-to-bin")
    os.symlink(w.inside, spelled)
    w.path(spelled)
    d = decide(w, "tool")
    assert_asks_the_owner(d)
    assert card(d)["path"] == os.path.join(spelled, "tool")
    if w.mode != "ask":
        w.reviewer.assert_not_called()
    assert not w.started


@POSIX_ONLY
def test_a_path_folder_spelled_through_a_link_into_auras_own_paths_is_refused(w):
    outside_target = make_exe(w.outside, "real-tool")
    own = os.path.join(w.aura_home, "bin")
    os.makedirs(own)
    os.symlink(outside_target, os.path.join(own, "tool"))
    spelled = os.path.join(w.outside, "link-to-own")
    os.symlink(own, spelled)
    w.path(spelled)
    assert (decide(w, "tool").kind, decide(w, "tool").reason_code) == ("deny", "FLOOR_AURA_WINDOW")


# -- AURA's own paths, the blocked list, unattended triggers --------------------------------------------------------------


@POSIX_ONLY
def test_a_program_inside_auras_own_paths_is_refused_even_through_a_link(w):
    entry = make_exe(os.path.join(w.aura_home, "bin"), "tool")
    w.path(os.path.dirname(entry))
    d = decide(w, "tool")
    assert (d.kind, d.reason_code) == ("deny", "FLOOR_AURA_WINDOW")
    os.symlink(entry, os.path.join(w.outside, "tool2"))
    w.path(w.outside)
    d = decide(w, "tool2")
    assert (d.kind, d.reason_code) == ("deny", "FLOOR_AURA_WINDOW")
    assert not w.started


def test_an_application_whose_resolved_file_name_is_blocked_is_refused(w, monkeypatch):
    script = make_exe(w.outside, "tool.cmd", b"echo hi\n")
    switch(monkeypatch, programs.WINDOWS, which=lambda n: script if n == "tool" else None)
    assert decide(w, "tool").reason_code != "FLOOR_BLOCKED_APP"
    w.db.add(models.DeviceAppBlock(user_id=w.user.id, app_id="tool-cmd"))
    w.db.commit()
    d = decide(w, "tool")
    assert (d.kind, d.reason_code) == ("deny", "FLOOR_BLOCKED_APP")


@POSIX_ONLY
def test_unattended_triggers_never_start_a_program_found_inside_a_folder(w):
    make_exe(w.inside, "tool")
    w.path(w.inside)
    for trigger in ("schedule", "automation", "inbound", "heartbeat"):
        assert decide(w, "tool", trigger).kind == "deny", trigger
    assert not w.started


def test_the_url_and_the_name_checks_are_unchanged(w):
    assert "script" in apps.open_app("notes.cmd")["error"]
    assert "app name" in apps.open_app("-x")["error"]
    assert "Only http and https" in apps.open_app("mailto:someone@example.com")["error"]
    d = decide(w, "https://example.com/page")
    assert d.reason_code != "UNCHECKED_OPEN"


# -- the resolver ---------------------------------------------------------------------------------------------------------


@POSIX_ONLY
def test_posix_resolves_through_path_without_following_links(tmp_path, monkeypatch):
    real = make_exe(tmp_path / "real", "tool")
    link = tmp_path / "bin" / "alias"
    link.parent.mkdir()
    os.symlink(real, link)
    monkeypatch.setenv("PATH", str(link.parent))
    monkeypatch.setattr(programs, "current_platform", lambda: programs.LINUX)
    assert programs.resolve("alias") == str(link)
    assert programs.resolve("missing") == "" and programs.resolve("") == ""


def test_windows_finds_a_script_by_its_bare_name_through_pathext(tmp_path, monkeypatch):
    script = tmp_path / "notes.cmd"
    script.write_text("echo hi")
    switch(monkeypatch, programs.WINDOWS, which=lambda n: str(script) if n == "notes" else None)
    assert programs.resolve("notes") == str(script)


def test_windows_falls_back_to_the_app_paths_registry(tmp_path, monkeypatch):
    exe = tmp_path / "Some App" / "some.exe"
    exe.parent.mkdir()
    exe.write_text("x")
    monkeypatch.setenv("APPROOT", str(tmp_path))
    plain = lambda n: None  # noqa: E731
    switch(monkeypatch, programs.WINDOWS, registry=[str(exe)], which=plain)
    assert programs.resolve("some") == str(exe)
    switch(monkeypatch, programs.WINDOWS, registry=[f'"{exe}"'], which=plain)
    assert programs.resolve("some") == str(exe)
    switch(monkeypatch, programs.WINDOWS, registry=['"%APPROOT%/Some App/some.exe"'], which=plain)
    assert os.path.normcase(programs.resolve("some")) == os.path.normcase(str(exe))
    # the first value that is an existing file wins; a missing file, a relative path and an empty value are skipped
    switch(monkeypatch, programs.WINDOWS, registry=["", "relative.exe", str(tmp_path / "gone.exe"), str(exe)], which=plain)
    assert programs.resolve("some") == str(exe)
    switch(monkeypatch, programs.WINDOWS, registry=[str(tmp_path / "gone.exe")], which=plain)
    assert programs.resolve("some") == ""


def test_windows_prefers_path_over_the_registry(tmp_path, monkeypatch):
    on_path = tmp_path / "x.exe"
    on_path.write_text("x")
    other = tmp_path / "y.exe"
    other.write_text("y")
    switch(monkeypatch, programs.WINDOWS, registry=[str(other)], which=lambda n: str(on_path))
    assert programs.resolve("x") == str(on_path)


def test_macos_matches_the_bundle_name_then_the_canonical_id_one_folder_below(tmp_path, monkeypatch):
    root = tmp_path / "Applications"
    (root / "Safari.app").mkdir(parents=True)
    (root / "Utilities" / "Terminal.app").mkdir(parents=True)
    (root / "Google Chrome.app").mkdir()
    (root / "Deep" / "More" / "Hidden.app").mkdir(parents=True)
    other = tmp_path / "Other"
    (other / "Safari.app").mkdir(parents=True)
    switch(monkeypatch, programs.MACOS, roots=(root, other))
    assert programs.resolve("safari") == str(root / "Safari.app")  # without case, first root first
    assert programs.resolve("Safari.app") == str(root / "Safari.app")
    assert programs.resolve("terminal") == str(root / "Utilities" / "Terminal.app")  # one folder below
    assert programs.resolve("chrome") == str(root / "Google Chrome.app")  # by canonical application id
    assert programs.resolve("hidden") == ""  # two folders below is not looked at
    assert programs.resolve("nothing-like-it") == "" and programs.resolve("") == ""


def test_macos_exact_name_wins_over_a_canonical_match_in_an_earlier_root(tmp_path, monkeypatch):
    first, second = tmp_path / "A", tmp_path / "B"
    (first / "Google Chrome.app").mkdir(parents=True)
    (second / "Chrome.app").mkdir(parents=True)
    switch(monkeypatch, programs.MACOS, roots=(first, second))
    assert programs.resolve("chrome") == str(second / "Chrome.app")


def test_the_application_roots_are_the_documented_ones():
    assert tuple(programs.APPLICATION_ROOTS) == ("/Applications", "/System/Applications",
                                                 "/System/Library/CoreServices", "~/Applications")


@pytest.mark.parametrize("platform,entry,roots,self_paths,expected", [
    (programs.LINUX, "/usr/bin/x", ["/home/u"], [], ("ok", "")),
    (programs.LINUX, "/home/u/bin/x", ["/home/u"], [], ("owner", "folder")),
    (programs.LINUX, "/home/u/bin/x", ["/home/u"], ["/home/u/bin"], ("self", "")),
    (programs.LINUX, "", ["/home/u"], [], ("nothing", "")),
    (programs.WINDOWS, "", ["/home/u"], [], ("owner", "unfound")),
    (programs.MACOS, "", [], [], ("owner", "unfound")),
    (programs.MACOS, "/Applications/X.app", ["/home/u"], [], ("ok", "")),
])
def test_the_judgement_table(platform, entry, roots, self_paths, expected):
    def self_check(path):
        return any(path == p or path.startswith(p + "/") for p in self_paths)

    j = programs.judge(entry, roots, self_check, platform)
    assert (j.kind, j.why) == expected


# -- the places that decide what opening a file runs ----------------------------------------------------------------------

NEW_PROTECTED = ("~/.config/mimeapps.list", "~/.local/share/applications", "~/.local/share/mime",
                 "~/.config/environment.d", "~/.pam_environment", "~/.xprofile", "~/.xsessionrc", "~/.xinitrc",
                 "~/.bash_logout", "~/.zlogout", "~/.config/powershell", "~/.local/share/systemd",
                 "~/Library/Preferences/com.apple.LaunchServices")


def test_the_protected_list_pins_the_additions(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    for entry in NEW_PROTECTED:
        assert entry in ALWAYS_PROTECTED
        assert os.path.abspath(os.path.expanduser(entry)) in settings.device_protected_path_list, entry
    before = list(settings.device_protected_path_list)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))  # the default spelling adds nothing
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".local" / "share"))
    assert settings.device_protected_path_list == before


def test_the_xdg_forms_are_protected_when_those_variables_are_set(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xc"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xd"))
    listed = settings.device_protected_path_list
    for rel in ("mimeapps.list", "environment.d", "powershell", "autostart", "git"):
        assert os.path.abspath(str(tmp_path / "xc" / rel)) in listed, rel
    for rel in ("applications", "mime", "systemd"):
        assert os.path.abspath(str(tmp_path / "xd" / rel)) in listed, rel


def test_the_file_tools_refuse_to_write_what_decides_how_a_file_opens(w, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    with bound(w):
        assert w.home in access.roots()
        for rel in (".config/mimeapps.list", ".local/share/applications/evil.desktop", ".local/share/mime/x.xml"):
            with pytest.raises(DeviceUnavailable):
                files.write_text(os.path.join(w.home, rel), "[Default Applications]\n")
            assert not os.path.exists(os.path.join(w.home, rel))
        files.write_text(os.path.join(w.work, "fine.txt"), "x")  # the connected folder itself still works


@pytest.mark.skipif(sys.platform != "linux", reason="the Linux listing")
def test_list_applications_still_lists_the_users_desktop_entries(w):
    folder = os.path.join(w.home, ".local", "share", "applications")
    os.makedirs(folder)
    with open(os.path.join(folder, "mine-only.desktop"), "w") as fh:
        fh.write("[Desktop Entry]\n")
    names = [a["name"] for a in apps.list_applications(limit=100000)]
    assert "mine-only" in names


# -- what is left of the old exec-mode settings at run time ---------------------------------------------------------------


def test_the_old_exec_mode_is_gone_at_run_time():
    from app.api.device import DevicePolicyOut
    from app.device import policy
    from app.device.runtime import capabilities

    assert not hasattr(policy, "describe_mode") and not hasattr(policy, "_allowlisted")
    assert "auto" not in policy.Verdict.__dataclass_fields__
    assert {"exec_mode", "exec_allowlist", "explanation"}.isdisjoint(DevicePolicyOut.model_fields)
    assert {"exec_mode", "exec_allowlist"}.isdisjoint(capabilities(include_policy=True).get("policy", {}))
    assert policy.classify("rm -rf ~/work").dangerous  # the danger wording stays
