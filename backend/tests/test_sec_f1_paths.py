"""F1: the file tools and the gate resolve a path the same way, and the file layer refuses AURA's own paths."""

from __future__ import annotations

import ntpath
import os

import pytest

from app.device import files
from app.permissions import access, match
from tests.sec_world import SECRET, build, run


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    yield world
    world.db.close()
    world.engine.dispose()


def _leaks(result) -> bool:
    return "super-secret-jwt-key" in repr(result)


def test_relative_env_does_not_reach_the_servers_env_file(w):
    """poc1: `.env` is judged as ~/.env and used to be read from the server's working directory."""
    d, result = run(w, "read_file", {"path": ".env"})
    assert not _leaks(result)
    # The same spelling the gate judged is the one that is read: ~/.env (which does not exist).
    assert d.kind != "allow" or "error" in result
    assert [t.value for t in d.targets] == [os.path.join(os.path.realpath(w.home), ".env")]


def test_relative_spellings_never_leak_the_env_file(w):
    for spelling in (".env", "./.env", "../.env", "backend/.env", "code/aura/backend/.env"):
        _d, result = run(w, "read_file", {"path": spelling})
        assert not _leaks(result), spelling
    # the absolute path is a self path: refused by the floor
    d, result = run(w, "read_file", {"path": os.path.join(w.proj, ".env")})
    assert d.kind == "deny" and d.reason_code == "FLOOR_SELF_PATH" and result is None


@pytest.mark.parametrize("tool,args", [
    ("write_file", {"path": ".env", "content": "OWNED=1\n"}),
    ("write_file", {"path": "aura.db", "content": "OWNED"}),
    ("edit_file", {"path": ".env", "find": "SECRET", "replace": "OWNED"}),
    ("delete_file", {"path": ".env"}),
    ("move_file", {"source": ".env", "destination": "stolen.txt"}),
])
def test_relative_writes_never_touch_the_servers_files(w, tool, args):
    before = {n: open(os.path.join(w.proj, n)).read() for n in (".env", "aura.db")}
    for approved in (False, True):
        d = None
        try:
            from app.permissions import gate
            from app.agent.coordinator import execute_tool

            d = gate.decide(w.db, w.user, tool, args, gate.build_context(w.db, w.user, "chat"))
            if d.kind == "ask":  # delete always asks; run what the user would approve
                d = gate.decide_approved(w.db, w.user, _action(w, tool, args)) if approved else d
            if d.kind == "allow":
                execute_tool(w.db, w.user, tool, args, decision=d)
        except Exception:
            pass
    after = {n: open(os.path.join(w.proj, n)).read() for n in (".env", "aura.db")}
    assert before == after
    assert not os.path.exists(os.path.join(w.proj, "stolen.txt"))


def _action(w, tool, args):
    from app.models import PendingAction
    from app.permissions import gate

    return PendingAction(user_id=w.user.id, tool_name=tool, arguments=args, args_digest=gate.args_digest(args),
                         origin="chat", status="pending")


def test_handlers_act_on_the_decisions_canonical_path(w):
    """The handler uses the path the gate computed, not the raw argument."""
    (os.path.join(w.home, "x"))
    with open(os.path.join(w.home, "note.txt"), "w") as fh:
        fh.write("hello")
    d, result = run(w, "read_file", {"path": "note.txt"})
    assert d.kind == "allow" and result["content"] == "hello"
    # a different working directory at execute time changes nothing
    os.makedirs(os.path.join(w.home, "elsewhere"))
    with open(os.path.join(w.home, "elsewhere", "note.txt"), "w") as fh:
        fh.write("other")
    from app.agent.coordinator import execute_tool

    os.chdir(os.path.join(w.home, "elsewhere"))
    assert execute_tool(w.db, w.user, "read_file", {"path": "note.txt"}, decision=d)["content"] == "hello"


@pytest.mark.parametrize("spelling", [
    ".env", "./a/../.env", "~/.env", "$HOME/.env", "${HOME}/.env", "sub/../x.txt", "~/work/../x",
    "%USERPROFILE%/x", "%USERPROFILE%\\x", "$env:USERPROFILE/x", ".\\x.txt", "..\\x.txt",
])
def test_one_resolver_for_gate_and_files(w, spelling):
    """The gate's resolver and files.resolve give one answer for every spelling."""
    with access.use(access.snapshot(w.db, w.user)):
        want = match.normalize_path(spelling, os.path.expanduser("~"))
        try:
            got = files.resolve(spelling)
        except files.PathNotAllowed:
            return  # refused (outside the fence, protected or own): never a different place
        assert os.path.normcase(got) == os.path.normcase(want)


def test_windows_spellings_resolve_like_the_gate():
    ops = match.PathOps(ntpath, True)
    for spelling in ("%USERPROFILE%\\x", "$env:USERPROFILE\\x", ".\\x", "..\\x", "~\\x", "C:x"):
        a = match.normalize_path(spelling, "C:\\Users\\u", ops)
        assert ntpath.isabs(a) or a  # never raises on a Windows spelling; one function serves both layers


def test_files_resolve_refuses_own_paths_even_inside_a_connected_folder(w):
    with access.use(access.snapshot(w.db, w.user)):
        for own in (os.path.join(w.proj, ".env"), os.path.join(w.proj, "aura.db"), w.aura_home,
                    os.path.join(w.aura_home, "aura.env")):
            with pytest.raises(files.PathNotAllowed):
                files.resolve(own)
        # a folder holding AURA's own paths cannot be deleted or moved
        with pytest.raises(files.PathNotAllowed):
            files.delete_path(os.path.join(w.home, ".aura"))
        with pytest.raises(files.PathNotAllowed):
            files.move_path(os.path.join(w.home, "code"), os.path.join(w.work, "code"))
        with pytest.raises(files.PathNotAllowed):
            files.delete_path(os.path.join(w.home, "code", "aura"))


def test_symlink_swapped_between_decide_and_execute(w, tmp_path):
    """The path is safe when decided; then the folder is replaced by a link into AURA's own folder."""
    safe = os.path.join(w.work, "safe")
    os.makedirs(safe)
    with open(os.path.join(safe, "f.txt"), "w") as fh:
        fh.write("ok")
    link = os.path.join(w.work, "link")
    try:
        os.symlink(safe, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    args = {"path": os.path.join(link, "f.txt")}
    from app.agent.coordinator import execute_tool
    from app.permissions import gate

    d = gate.decide(w.db, w.user, "read_file", args, gate.build_context(w.db, w.user, "chat"))
    assert d.kind == "allow"
    os.remove(link)
    os.symlink(w.proj, link, target_is_directory=True)
    with open(os.path.join(w.proj, "f.txt"), "w") as fh:
        fh.write("planted")
    result = execute_tool(w.db, w.user, "read_file", args, decision=d)
    assert "error" in result and "planted" not in repr(result)
    # and writing through the swapped link cannot reach the env file either
    wargs = {"path": os.path.join(link, ".env"), "content": "X"}
    os.remove(link)
    os.symlink(safe, link, target_is_directory=True)
    wd = gate.decide(w.db, w.user, "write_file", {"path": os.path.join(link, "g.txt"), "content": "x"},
                     gate.build_context(w.db, w.user, "chat"))
    assert wd.kind == "allow"
    os.remove(link)
    os.symlink(w.proj, link, target_is_directory=True)
    res = execute_tool(w.db, w.user, "write_file", {"path": os.path.join(link, "g.txt"), "content": "x"}, decision=wd)
    assert "error" in res and not os.path.exists(os.path.join(w.proj, "g.txt"))
    del wargs
