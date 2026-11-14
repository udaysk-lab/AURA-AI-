"""Windows path forms (second review): 8.3 short names, drive-relative paths, UNC paths to this computer and
`\\\\?\\UNC\\`. They run with Windows semantics (ntpath) on every platform, and natively on the Windows runner."""

from __future__ import annotations

import ntpath
import os

import pytest

from app.permissions import floors, gate, match, pathglob, shellparse
from tests.sec_world import build

NT = match.PathOps(ntpath, True)
HOME = r"C:\Users\u"


def test_a_short_name_may_be_aura_s_folder():
    assert pathglob.could_match(r"C:\Users\u\AURA~1\aura.env", r"C:\Users\u\.aura", r"C:\Users\u\work", HOME,
                                ci=True, windows=True)
    assert pathglob.opaque_prefix(r"C:\Users\u\AURA~1\aura.env", HOME, HOME, True) == r"C:/Users/u"
    assert pathglob.opaque_prefix(r"C:\Users\u\work\a.txt", HOME, HOME, True) is None
    assert pathglob.opaque_prefix(r"C:\Users\u\AURA~1\aura.env", HOME, HOME, False) is None  # not Windows


def test_a_drive_relative_path_is_resolved_against_the_working_directory():
    assert pathglob.absolute_components(r"C:.aura\aura.env", HOME, HOME, True) == ["C:", "Users", "u", ".aura", "aura.env"]
    assert match.normalize_path(r"C:.aura\aura.env", HOME, NT) == r"C:\Users\u\.aura\aura.env"
    assert pathglob.could_match(r"C:.aura\aura.env", r"C:\Users\u\.aura", HOME, HOME, ci=True, windows=True)


def test_a_drive_relative_path_on_another_drive_is_opaque():
    assert pathglob.opaque_prefix(r"D:secret\x", HOME, HOME, True) is not None
    with pytest.raises(match.BadArgs):
        match.normalize_path(r"D:secret\x", HOME, NT)


@pytest.mark.parametrize("raw", [
    r"\\127.0.0.1\c$\Users\u\work\finance\x",
    r"\\?\UNC\127.0.0.1\c$\Users\u\work\finance\x",
    r"\\localhost\c$\Users\u\work\finance\x",
    r"\\LOCALHOST\C$\Users\u\work\finance\x",
    r"\\[::1]\c$\Users\u\work\finance\x",
    r"\\0--1.ipv6-literal.net\c$\Users\u\work\finance\x",
    r"//127.0.0.1/c$/Users/u/work/finance/x",
    r"\\?\C:\Users\u\work\finance\x",
])
def test_a_unc_path_to_this_computer_is_a_drive_path(raw):
    got = match.normalize_path(raw, HOME, NT)
    assert got == r"C:\Users\u\work\finance\x", got
    assert match.path_matches(r"C:\Users\u\work\finance\**", got, NT)
    assert pathglob.components(raw, windows=True)[:3] == ["C:", "Users", "u"]


def test_this_host_name_counts(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "gethostname", lambda: "My-PC")
    got = match.normalize_path(r"\\my-pc\c$\Users\u\x", HOME, NT)
    assert got == r"C:\Users\u\x"


def test_a_share_on_another_computer_is_not_rewritten():
    assert pathglob.opaque_prefix(r"\\fileserver\c$\x", HOME, HOME, True) is not None
    assert not match.normalize_path(r"\\fileserver\c$\x", HOME, NT).upper().startswith("C:")


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip")
    yield world
    world.db.close()
    world.engine.dispose()


def decide(w, command, dialect, workdir=None):
    gate._dialect = lambda p: dialect
    return gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": workdir or w.work},
                       gate.build_context(w.db, w.user, "chat"))


def test_a_short_name_under_the_folder_that_holds_aura_s_files_is_refused(w):
    d = decide(w, r"Get-Content ~\AURA~1\aura.env", "pwsh")
    assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH", (d.kind, d.reason_code)
    d = decide(w, r"type ~\AURA~1\aura.env", "cmd")
    assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH", (d.kind, d.reason_code)


def test_a_short_name_elsewhere_always_needs_the_user(w):
    d = decide(w, r"Get-Content ~\work\FINANC~1\x.txt", "pwsh")
    assert d.kind == "ask" and d.always_user and tuple(d.options) == ("once", "reject")
    d = decide(w, r"type ~\work\FINANC~1\x.txt", "cmd")
    assert d.kind == "ask" and d.always_user


@pytest.mark.skipif(os.name != "nt", reason="native Windows paths")
def test_native_windows_unc_and_short_names(w):
    d = decide(w, rf"type \\127.0.0.1\c$\{os.path.splitdrive(w.aura_home)[1].lstrip(chr(92))}\aura.env", "cmd")
    assert d.kind == "deny"
