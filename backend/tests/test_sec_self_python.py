"""The Python that runs AURA counts as AURA: its interpreter, its libraries and its virtual environment."""

from __future__ import annotations

import json
import os
import sys
import sysconfig

import pytest

from app.device import programs
from app.permissions import floors
from tests.sec_world import build, run


@pytest.fixture(autouse=True)
def fresh_interpreter_paths():
    floors._interpreter_paths.cache_clear()
    yield
    floors._interpreter_paths.cache_clear()


def test_the_running_interpreter_and_its_libraries_are_self_paths():
    assert floors.in_self_path(os.path.realpath(json.__file__))
    assert floors.in_self_path(os.path.realpath(sys.executable))
    base = getattr(sys, "_base_executable", "")
    if base:
        assert floors.in_self_path(os.path.realpath(base))
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        assert floors.in_self_path(os.path.join(os.path.realpath(sysconfig.get_path(key)), "anything.py")), key
    import site

    # On Windows site.getsitepackages() also names the installation root, which holds other programs: only the
    # library folders below it count (the root of a virtual environment is checked just below).
    roots = {os.path.normcase(os.path.realpath(p)) for p in (sys.prefix, sys.base_prefix)}
    for folder in site.getsitepackages():
        if os.path.normcase(os.path.realpath(folder)) in roots:
            continue
        assert floors.in_self_path(os.path.join(os.path.realpath(folder), "module.py")), folder
    if sys.prefix != sys.base_prefix:
        assert floors.in_self_path(os.path.join(os.path.realpath(sys.prefix), "pyvenv.cfg"))


@pytest.mark.skipif(not os.path.exists("/usr/bin/env"), reason="needs a system program")
def test_judge_says_ok_for_an_existing_system_program():
    assert programs.judge("/usr/bin/env", (), floors.in_self_path, programs.LINUX).kind == "ok"
    assert programs.judge(sys.executable, (), floors.in_self_path, programs.LINUX).kind == "self"


def test_the_system_prefix_as_a_whole_is_not_listed():
    listed = [os.path.realpath(p) for p in floors.self_paths()]
    if sys.prefix == sys.base_prefix:
        assert os.path.realpath(sys.prefix) not in listed
    assert os.path.realpath(sys.base_prefix) not in listed or sys.prefix != sys.base_prefix


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode="skip", connect_home=True)
    # A per-user interpreter under the connected home folder (python.org "for me only", pyenv, conda).
    base = os.path.join(world.home, ".pyenv", "versions", "3.12.4")
    lib = os.path.join(base, "lib", "python3.12")
    os.makedirs(os.path.join(lib, "json"))
    os.makedirs(os.path.join(lib, "site-packages", "pkg"))
    os.makedirs(os.path.join(base, "bin"))
    for rel in ("json/__init__.py", "site-packages/pkg/__init__.py"):
        with open(os.path.join(lib, rel), "w") as fh:
            fh.write("# library\n")
    exe = os.path.join(base, "bin", "python3")
    with open(exe, "w") as fh:
        fh.write("binary")
    real = sysconfig.get_path

    def get_path(key, *a, **kw):
        if key in ("stdlib", "platstdlib"):
            return lib
        if key in ("purelib", "platlib"):
            return os.path.join(lib, "site-packages")
        return real(key, *a, **kw)

    monkeypatch.setattr(sysconfig, "get_path", get_path)
    monkeypatch.setattr(sys, "executable", exe)
    monkeypatch.setattr(sys, "_base_executable", exe, raising=False)
    floors._interpreter_paths.cache_clear()
    world.lib, world.exe = lib, exe
    yield world
    world.db.close()
    world.engine.dispose()


@pytest.mark.parametrize("rel", ["json/__init__.py", "site-packages/pkg/__init__.py", "json/new.py"])
def test_a_library_module_of_a_per_user_python_cannot_be_written(w, rel):
    path = os.path.join(w.lib, *rel.split("/"))
    for tool, args in (("write_file", {"path": path, "content": "import os\n"}),
                       ("edit_file", {"path": path, "find": "a", "replace": "b"}),
                       ("delete_file", {"path": path})):
        d, _ = run(w, tool, args)
        assert (d.kind, d.reason_code) == ("deny", "FLOOR_SELF_PATH"), (tool, rel, d.reason_code)
    with open(os.path.join(w.lib, "json", "__init__.py")) as fh:
        assert fh.read() == "# library\n"


def test_the_interpreter_file_cannot_be_replaced_or_moved(w):
    d, _ = run(w, "write_file", {"path": w.exe, "content": "x"})
    assert (d.kind, d.reason_code) == ("deny", "FLOOR_SELF_PATH")
    d, _ = run(w, "move_file", {"source": w.exe, "destination": os.path.join(w.work, "py")})
    assert (d.kind, d.reason_code) == ("deny", "FLOOR_SELF_PATH")


def test_a_folder_holding_the_libraries_cannot_be_moved_or_deleted(w):
    d, _ = run(w, "delete_file", {"path": os.path.join(w.home, ".pyenv")})
    assert d.kind == "deny" and d.reason_code in ("FLOOR_SELF_PATH", "FLOOR_PROTECTED_PATH")


def test_a_file_next_to_them_is_still_an_ordinary_file(w):
    d, out = run(w, "write_file", {"path": os.path.join(w.work, "notes.txt"), "content": "x"})
    assert d.kind == "allow" and "error" not in out


def test_a_command_that_names_the_interpreter_to_run_it_is_not_a_change_to_it(w):
    assert floors.in_self_path(w.exe)
    assert not floors.in_self_path(w.exe, shell=True)
    assert floors.in_self_path(os.path.join(w.lib, "json", "x.py"), shell=True)
