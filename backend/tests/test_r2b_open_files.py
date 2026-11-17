"""R2b A: open_app with a file. A document or media type follows mode and rules; anything that could run as a
program needs the owner every time, in every mode (UNCHECKED_OPEN), judged on the canonical real path, and the
handler opens exactly that path."""

from __future__ import annotations

import io
import os
import sys
import zipfile
from datetime import timedelta
from unittest import mock

import pytest

from app.agent.coordinator import execute_tool
from app.device import apps, filetypes
from app.llm import ToolCall
from app.models import utcnow
from app.permissions import approvals, gate, reviewer, rules
from tests.sec_world import build, run

MODES = ("skip", "auto", "ask")

# The allow-list is pinned: adding a type is a decision, not an accident.
EXPECTED_ALLOWED = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".heif",
    ".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac",
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpg", ".mpeg",
    ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".odg",
}

ASKING = [
    ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".js", ".wsf", ".hta", ".reg", ".py", ".sh", ".command", ".scpt",
    ".exe", ".com", ".msi", ".msp", ".dll", ".jar", ".appimage", ".run", ".scr", ".pif", ".cpl",
    ".lnk", ".url", ".desktop", ".webloc", ".html", ".htm", ".svg", ".xhtml", ".xml", ".json", ".yml",
    ".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".doc", ".xls", ".ppt", ".rtf",
    ".zip", ".7z", ".rar", ".tar", ".gz", ".iso", ".dmg", ".pkg", ".deb", ".rpm", ".apk",
    ".weird", ".xyz123", "",
]


def _zip(*names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        for n in names:
            z.writestr(n, "x")
    return buf.getvalue()


CONTENT = {
    ".pdf": b"%PDF-1.4\n", ".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff\xe0", ".jpeg": b"\xff\xd8\xff\xe0",
    ".gif": b"GIF89a", ".docx": _zip("word/document.xml"), ".xlsx": _zip("xl/workbook.xml"),
    ".pptx": _zip("ppt/presentation.xml"), ".odt": _zip("content.xml"), ".ods": _zip("content.xml"),
    ".odp": _zip("content.xml"), ".odg": _zip("content.xml"),
}


def make(w, name, content=None, ext=None):
    path = os.path.join(w.work, name)
    data = content if content is not None else CONTENT.get(ext if ext is not None else os.path.splitext(name)[1],
                                                           b"plain words\n")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


@pytest.fixture(params=MODES)
def w(request, tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode=request.param)
    world.mode = request.param
    verdict = mock.Mock(return_value=reviewer.ReviewerVerdict(True, "fine", ""))
    monkeypatch.setattr(reviewer, "available", lambda: True)
    monkeypatch.setattr(reviewer, "review", verdict)
    world.reviewer = verdict
    world.opened = []
    monkeypatch.setattr(apps, "_open_target", lambda target, label: world.opened.append(target) or {"message": label})
    yield world
    world.db.close()
    world.engine.dispose()


def decide(w, path, trigger="chat"):
    return gate.decide(w.db, w.user, "open_app", {"name": path}, gate.build_context(w.db, w.user, trigger))


def assert_asks_the_owner(d):
    assert d.kind == "ask" and d.reason_code == "UNCHECKED_OPEN", (d.kind, d.reason_code)
    assert d.source == "always_user" and d.always_user
    assert tuple(d.options) == ("once", "reject") and d.proposed_rule is None


def test_the_allow_list_is_pinned():
    assert set(filetypes.OPEN_ALLOWED) == EXPECTED_ALLOWED
    for bad in (".html", ".htm", ".svg", ".docm", ".xlsm", ".pptm", ".lnk", ".url", ".desktop", ".webloc", ".bat",
                ".exe", ".zip", ".doc", ".rtf", ".js", ""):
        assert bad not in filetypes.OPEN_ALLOWED


@pytest.mark.parametrize("ext", sorted(EXPECTED_ALLOWED))
def test_an_allow_listed_type_follows_the_mode(w, ext):
    path = make(w, "file" + ext, ext=ext)
    d = decide(w, path)
    if w.mode == "skip":
        assert (d.kind, d.reason_code) == ("allow", "MODE_SKIP")
    elif w.mode == "ask":
        assert (d.kind, d.reason_code) == ("ask", "MODE_ASK") and not d.always_user
    else:
        assert (d.kind, d.reason_code) == ("allow", "REVIEWER_ALLOW")
        w.reviewer.assert_called()


@pytest.mark.parametrize("ext", ASKING)
def test_everything_else_asks_every_time_in_every_mode(w, ext):
    path = make(w, "thing" + ext, b"echo hi\n" if ext not in CONTENT else None)
    assert_asks_the_owner(decide(w, path))
    w.reviewer.assert_not_called()
    assert not w.opened


def test_a_file_without_an_extension_and_a_dotfile_ask(w):
    for name in ("README", "Makefile", ".bashrc", "notes."):
        assert_asks_the_owner(decide(w, make(w, name)))


def test_a_script_the_agent_just_wrote_asks_even_in_skip_approvals(w):
    d, out = run(w, "write_file", {"path": os.path.join(w.work, "x.bat"), "content": "echo hi"})
    if w.mode == "ask":
        assert d.kind == "ask"
        open(os.path.join(w.work, "x.bat"), "w").write("echo hi")
    else:
        assert d.kind == "allow", (d.kind, d.reason_code)
    d = decide(w, os.path.join(w.work, "x.bat"))
    assert_asks_the_owner(d)
    d2, result = run(w, "open_app", {"name": os.path.join(w.work, "x.bat")})
    assert result is None and not w.opened


def test_a_folder_follows_the_mode(w):
    os.mkdir(os.path.join(w.work, "stuff"))
    d = decide(w, os.path.join(w.work, "stuff"))
    assert d.reason_code in ("MODE_SKIP", "MODE_ASK", "REVIEWER_ALLOW") and not d.always_user


def test_a_folder_that_is_a_bundle_asks(w):
    os.mkdir(os.path.join(w.work, "Thing.app"))
    assert_asks_the_owner(decide(w, os.path.join(w.work, "Thing.app")))


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need a privilege on Windows")
def test_a_link_is_judged_by_what_it_resolves_to(w):
    script = make(w, "run.bat", b"echo hi\n")
    note = make(w, "note.txt")
    os.symlink(script, os.path.join(w.work, "looks-harmless.txt"))
    os.symlink(note, os.path.join(w.work, "looks-risky.bat"))
    os.symlink(w.work, os.path.join(w.home, "folder-link"))
    assert_asks_the_owner(decide(w, os.path.join(w.work, "looks-harmless.txt")))
    d = decide(w, os.path.join(w.work, "looks-risky.bat"))
    assert d.reason_code != "UNCHECKED_OPEN" and not d.always_user
    t = [x for x in d.targets if x.kind == "path"][0]
    assert t.value == os.path.realpath(note)


@pytest.mark.parametrize("ext,content", [
    (".txt", b"MZ\x90\x00 a program"), (".txt", b"\x7fELF\x02\x01"), (".txt", b"#!/bin/sh\necho hi\n"),
    (".pdf", b"just words"), (".png", b"#!/bin/sh\n"), (".jpg", b"MZ\x90\x00"),
    (".docx", b"not a zip at all"), (".xlsx", _zip("xl/vbaProject.bin")), (".docx", _zip("word/vbaProject.bin")),
    (".odt", _zip("Basic/Standard/Module1.xml")), (".odt", _zip("Scripts/python/x.py")),
])
def test_a_renamed_file_is_judged_by_what_it_is(w, ext, content):
    assert_asks_the_owner(decide(w, make(w, "renamed" + ext, content)))


@pytest.mark.skipif(os.name == "nt", reason="the executable bit is a POSIX notion")
def test_a_document_marked_as_a_program_asks(w):
    path = make(w, "marked.txt")
    os.chmod(path, 0o755)
    assert_asks_the_owner(decide(w, path))


def test_no_rule_window_or_reviewer_covers_an_asking_file(w):
    script = make(w, "run.bat", b"echo hi\n")
    for kwargs in (
        dict(tool="open_app", target_kind="", pattern=""),
        dict(tool=None, target_kind="", pattern=""),
        dict(tool=None, target_kind="path", pattern=os.path.dirname(script) + "/**"),
        dict(tool=None, target_kind="path", pattern=script),
        dict(tool="open_app", target_kind="", pattern="", expires_at=utcnow() + timedelta(minutes=10)),
    ):
        rules.add(w.db, w.user, effect="allow", system="device-control", actor="user", **kwargs)
        w.db.commit()
        assert_asks_the_owner(decide(w, script))
    w.reviewer.assert_not_called()


def test_a_block_rule_and_the_floors_still_come_first(w):
    script = make(w, "run.bat", b"echo hi\n")
    rules.add(w.db, w.user, effect="block", system="device-control", tool=None, target_kind="path",
              pattern=script, actor="user")
    w.db.commit()
    d = decide(w, script)
    assert d.kind == "deny" and d.reason_code == "RULE_BLOCK"
    d = decide(w, os.path.join(w.aura_home, "aura.env"))
    assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH"


def test_unattended_triggers_never_open_files(w):
    script = make(w, "run.bat", b"echo hi\n")
    note = make(w, "note.txt")
    for trigger in ("schedule", "automation", "inbound", "heartbeat"):
        for path in (script, note):
            assert decide(w, path, trigger).kind == "deny", trigger


def test_an_app_name_and_a_url_are_unchanged(w, monkeypatch):
    from app.device import programs

    monkeypatch.setattr(programs, "current_platform", lambda: programs.LINUX)
    d = decide(w, "Spotify")
    assert d.reason_code != "UNCHECKED_OPEN"
    d = decide(w, "https://example.com/page")
    assert d.reason_code != "UNCHECKED_OPEN"


# -- the handler opens exactly the decided path ------------------------------------------------------------------


def park(w, path):
    ctx = gate.build_context(w.db, w.user, "chat")
    args = {"name": path}
    d = gate.decide(w.db, w.user, "open_app", args, ctx)
    assert_asks_the_owner(d)
    return approvals.queue_action(w.db, w.user, ctx, ToolCall(id="c1", name="open_app", arguments=args), d)


def test_an_approved_file_opens_exactly_the_real_path_that_was_shown(w):
    script = make(w, "run.bat", b"echo hi\n")
    action = park(w, script)
    assert action.card_payload["unchecked_open"]["path"] == os.path.realpath(script)
    assert "may run" in action.reason and "cannot check" in action.reason
    out = approvals.resolve(w.db, w.user, action.id, "once")
    assert out["status"] == "approved", out
    assert [os.path.normcase(p) for p in w.opened] == [os.path.normcase(os.path.realpath(script))]


def test_rejecting_opens_nothing(w):
    action = park(w, make(w, "run.bat", b"echo hi\n"))
    assert approvals.resolve(w.db, w.user, action.id, "reject")["status"] == "rejected"
    assert not w.opened


def test_only_once_or_reject_can_answer_the_card(w):
    action = park(w, make(w, "run.bat", b"echo hi\n"))
    for answer in ("window", "always"):
        with pytest.raises(approvals.ApprovalError):
            approvals.resolve(w.db, w.user, action.id, answer)
    assert not w.opened


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need a privilege on Windows")
def test_a_link_changed_after_the_card_is_refused(w):
    first = make(w, "first.bat", b"echo 1\n")
    second = make(w, "second.bat", b"echo 2\n")
    link = os.path.join(w.work, "go.bat")
    os.symlink(first, link)
    action = park(w, link)
    os.remove(link)
    os.symlink(second, link)
    out = approvals.resolve(w.db, w.user, action.id, "once")
    assert out["status"] == "refused" and not w.opened


def test_a_document_swapped_for_a_script_before_it_opens_is_refused(w):
    if w.mode == "ask":
        pytest.skip("the document would ask first; the swap is covered by the handler call below")
    path = make(w, "doc.txt")
    d = decide(w, path)
    assert d.kind == "allow"
    with open(path, "wb") as fh:
        fh.write(b"#!/bin/sh\necho hi\n")
    out = execute_tool(w.db, w.user, "open_app", {"name": path}, decision=d)
    assert "error" in out and not w.opened


def test_a_name_that_became_a_file_since_the_decision_is_refused(w, monkeypatch):
    if w.mode == "ask":
        pytest.skip("an app name asks in Ask first")
    monkeypatch.chdir(w.work)
    d = decide(w, "Spotify")
    assert d.kind == "allow" and not any(t.kind == "path" for t in d.targets)
    make(w, "Spotify", b"#!/bin/sh\n")
    out = execute_tool(w.db, w.user, "open_app", {"name": "Spotify"}, decision=d)
    assert "error" in out and not w.opened


def test_the_handler_alone_never_opens_a_program_without_the_owner(w):
    script = make(w, "run.bat", b"echo hi\n")
    out = apps.open_app(script)
    assert "error" in out and not w.opened
    out = apps.open_app(script, decided=os.path.realpath(script))
    assert "error" in out and not w.opened
    from app.permissions import access

    token = access.CURRENT_ACCESS.set(access.snapshot(w.db, w.user))
    try:
        out = apps.open_app(script, decided=os.path.realpath(script), owner_approved=True)
    finally:
        access.CURRENT_ACCESS.reset(token)
    assert "error" not in out and len(w.opened) == 1


def test_filetypes_classify_directly(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert filetypes.classify(str(f)).kind == "document"
    assert filetypes.classify(str(tmp_path)).kind == "folder"
    assert filetypes.classify(str(tmp_path / "nope.txt")).kind == "missing"
    assert filetypes.classify(os.path.realpath(sys.executable)).kind == "unchecked"
