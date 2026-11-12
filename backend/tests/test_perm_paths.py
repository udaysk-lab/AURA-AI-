"""Path normalisation and matching (spec 10 section 5.1). Floor checks are tested with WP4."""

from __future__ import annotations

import ntpath
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aura.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest  # noqa: E402

from app.permissions import match  # noqa: E402
from app.permissions.catalogue import CATALOGUE  # noqa: E402
from app.permissions.match import BadArgs, PathOps, PatternError, RuleSpec  # noqa: E402

WIN = PathOps(ntpath, True)
POSIX_CS = PathOps(os.path, False)


def rule(effect, pattern, id_="r"):
    return RuleSpec(id_, effect, "path", pattern)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "Work"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("x")
    (root / "sub" / "b.csv").write_text("x")
    return root


def norm(p):
    return match.normalize_path(str(p))


def test_exact_tree_and_glob(tree):
    t = norm(tree / "sub" / "b.csv")
    assert match.path_matches(norm(tree / "sub" / "b.csv"), t, POSIX_CS)
    assert match.path_matches(match.normalise_path_pattern(str(tree) + "/**"), t, POSIX_CS)
    assert match.path_matches(match.normalise_path_pattern(str(tree)) + os.sep + "**", norm(tree), POSIX_CS)  # dir itself
    assert match.path_matches(match.normalise_path_pattern(str(tree / "sub") + "/*.csv"), t, POSIX_CS)
    assert not match.path_matches(match.normalise_path_pattern(str(tree / "sub") + "/*.txt"), t, POSIX_CS)


def test_glob_last_segment_only(tree):
    assert match.normalise_path_pattern(str(tree / "*.txt")).endswith("*.txt")
    for bad in (str(tree / "*" / "x.txt"), str(tree) + "/**/x.txt", str(tree / "a**"), str(tree) + "/*/**", "", "  "):
        with pytest.raises(PatternError):
            match.normalise_path_pattern(bad)
    # a glob does not reach into sub-folders
    pat = match.normalise_path_pattern(str(tree / "*.csv"))
    assert not match.path_matches(pat, norm(tree / "sub" / "b.csv"), POSIX_CS)


def test_dotdot(tree):
    pat = match.normalise_path_pattern(str(tree / "sub") + "/**")
    sneaky = match.normalize_path(str(tree / "sub" / ".." / "a.txt"))
    assert sneaky == norm(tree / "a.txt")
    assert not match.path_matches(pat, sneaky, POSIX_CS)
    assert match.path_matches(pat, match.normalize_path(str(tree / "sub" / "x" / ".." / "y")), POSIX_CS)
    # sibling with the same prefix is not inside
    assert not match.path_matches(match.normalise_path_pattern(str(tree / "sub") + "/**"), norm(tree / "sub2" / "f"), POSIX_CS)


def test_symlink_out_of_folder(tree, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    link = tree / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    pat = match.normalise_path_pattern(str(tree) + "/**")
    target = match.normalize_path(str(link / "secret.txt"))
    assert target == norm(outside / "secret.txt")
    assert not match.path_matches(pat, target, POSIX_CS)


def test_case_insensitive_mac_windows(tree, monkeypatch):
    upper = str(tree).upper() + "/A.TXT"
    t = norm(tree / "a.txt")
    pat = match.normalise_path_pattern(str(tree) + "/**")
    monkeypatch.setattr(match, "_case_insensitive", lambda: False)
    assert not match.path_matches(pat, upper)
    monkeypatch.setattr(match, "_case_insensitive", lambda: True)
    assert match.path_matches(pat, upper)
    assert match.path_matches(t.upper(), t)
    assert match.fold("ABC", always=True) == "abc"
    monkeypatch.setattr(match, "_case_insensitive", lambda: False)
    assert match.fold("ABC") == "ABC" and match.fold("ABC", always=True) == "abc"
    # Windows rules, on any OS
    assert match.path_matches("C:\\Work\\**", "c:\\work\\Sub\\A.TXT", WIN)
    assert match.path_matches("C:\\Work\\*.CSV", "c:\\WORK\\b.csv", WIN)
    assert not match.path_matches("C:\\Work\\*.csv", "c:\\work\\sub\\b.csv", WIN)


def test_different_drive():
    assert not match.path_matches("C:\\Work\\**", "D:\\Work\\a.txt", WIN)
    assert not match.path_matches("C:\\Work\\a.txt", "D:\\Work\\a.txt", WIN)
    assert match.path_matches("C:\\Work\\**", "C:\\Work", WIN)
    assert not match.path_matches("C:\\Work\\**", "C:\\Workshop\\a", WIN)


def test_move_needs_both(tree):
    a = str(tree / "a.txt")
    dest = str(tree / "sub" / "moved.txt")
    t = match.extract(CATALOGUE["move_file"], {"source": a, "destination": dest})
    assert [(x.role, x.matchable) for x in t] == [("delete", True), ("write", True)]
    pat_src = rule("allow", match.normalise_path_pattern(str(tree) + "/*.txt"), "src")
    pat_dst = rule("allow", match.normalise_path_pattern(str(tree / "sub") + "/**"), "dst")
    assert match.allow_cover([pat_src], t) is None
    assert [r.id for r in match.allow_cover([pat_src, pat_dst], t)] == ["src", "dst"]
    # block / ask: either path is enough
    assert match.first_match("block", [rule("block", pat_dst.pattern, "b")], t).id == "b"
    assert match.first_match("ask", [rule("ask", pat_src.pattern, "k")], t).id == "k"


def test_extract_paths_and_defaults(tmp_path):
    home = str(tmp_path)
    t = match.extract(CATALOGUE["read_file"], {"path": "docs/x.txt"}, home=home)
    assert t[0].value == match.normalize_path("docs/x.txt", home) and t[0].role == "read"
    assert match.extract(CATALOGUE["list_directory"], {}, home=home)[0].value == match.normalize_path("~")
    assert match.extract(CATALOGUE["search_files"], {"query": "q", "root": home}, home=home)[0].value == norm(home)
    wd = match.extract(CATALOGUE["run_command"], {"command": "ls", "workdir": home}, home=home)
    assert [(x.kind, x.matchable) for x in wd] == [("path", False), ("command", True)] or [(x.kind) for x in wd] == ["command", "path"]
    with pytest.raises(BadArgs):
        match.extract(CATALOGUE["read_file"], {}, home=home)
    with pytest.raises(BadArgs):
        match.extract(CATALOGUE["write_file"], {"path": 5}, home=home)
    with pytest.raises(BadArgs):
        match.extract(CATALOGUE["write_file"], {"path": "a\x00b"}, home=home)


def test_shell_home_forms_expand(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for form in ("~/x", "$HOME/x", "${HOME}/x"):
        assert match.normalize_path(form) == match.normalize_path(str(tmp_path / "x")), form


# ---------------------------------------------------------------------------
# Floors for file tools (spec 10 section 8.2), WP4
# ---------------------------------------------------------------------------

import sys  # noqa: E402

from app.config import settings  # noqa: E402
from app.permissions import floors  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home folder, so `~` never means the developer's real one."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setattr(settings, "aura_home", "", raising=False)
    monkeypatch.setattr(settings, "device_extra_protected_paths", "", raising=False)
    return h


def floor_for(tool, **args):
    return floors.check(None, None, CATALOGUE[tool], match.extract(CATALOGUE[tool], args))


def test_self_paths_from_source_run(tmp_path, monkeypatch, home):
    """A from-source run keeps ./aura.db, .env and ../.env next to the code; all are AURA's own."""
    work = tmp_path / "repo" / "backend"
    work.mkdir(parents=True)
    monkeypatch.chdir(work)
    monkeypatch.setattr(settings, "database_url", "sqlite:///./aura.db", raising=False)
    monkeypatch.setattr(settings, "aura_config_file", "", raising=False)
    own = floors.self_paths()
    real = os.path.realpath
    for name in ("aura.db", "aura.db-wal", "aura.db-shm", "aura.db-journal", ".env"):
        assert real(str(work / name)) in own, name
    assert real(str(work.parent / ".env")) in own
    assert real(str(home / ".aura")) in own
    # the code's own folder (the directory holding the `app` package)
    import app

    assert real(os.path.join(os.path.dirname(app.__file__), os.pardir)) in own
    for name in ("aura.db", "aura.db-wal", ".env"):
        f = floor_for("read_file", path=str(work / name))
        assert f.code == "FLOOR_SELF_PATH", name
    assert floor_for("write_file", path=str(work.parent / ".env"), content="x").code == "FLOOR_SELF_PATH"
    # an ordinary file next to it is not
    assert not floor_for("read_file", path=str(tmp_path / "notes.txt")).deny


def test_desktop_home_and_install(tmp_path, monkeypatch, home):
    """The desktop app: its data folder, config file and install folder (or .app bundle)."""
    data = tmp_path / "AuraData"
    monkeypatch.setattr(settings, "aura_home", str(data), raising=False)
    monkeypatch.setattr(settings, "aura_config_file", str(data / "aura.env"), raising=False)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{data / 'aura.db'}", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    flat = tmp_path / "Programs" / "AURA"
    flat.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(flat / "AURA.exe"))
    assert floors.install_dir() == os.path.realpath(flat)

    bundle = tmp_path / "Applications" / "AURA.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(bundle / "Contents" / "MacOS" / "AURA"))
    assert floors.install_dir() == os.path.realpath(bundle)

    own = floors.self_paths()
    for p in (data, data / "aura.env", data / "aura.db", bundle):
        assert os.path.realpath(str(p)) in own
    for target in (data / "aura.env", data / "logs" / "x.log", bundle / "Contents" / "Resources" / "app.py"):
        assert floor_for("read_file", path=str(target)).code == "FLOOR_SELF_PATH", target
    assert floor_for("list_directory", path=str(data)).code == "FLOOR_SELF_PATH"


def test_protected_paths_any_role_and_any_spelling(tmp_path, monkeypatch, home):
    secret = tmp_path / "keys"
    secret.mkdir()
    monkeypatch.setattr(settings, "device_extra_protected_paths", str(secret), raising=False)
    for tool, args in (
        ("read_file", {"path": str(secret / "id")}),
        ("write_file", {"path": str(secret / "id"), "content": "x"}),
        ("list_directory", {"path": str(secret)}),
        ("read_file", {"path": str(secret / "sub" / ".." / "id")}),
        ("read_file", {"path": str(secret).upper() + "/id"}),  # case folded on every OS
    ):
        assert floor_for(tool, **args).code == "FLOOR_PROTECTED_PATH", (tool, args)
    # a symlink out of a harmless folder into the protected one
    link = tmp_path / "harmless"
    try:
        os.symlink(secret, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    assert floor_for("read_file", path=str(link / "id")).code == "FLOOR_PROTECTED_PATH"


def test_delete_or_move_of_a_folder_holding_a_protected_path(tmp_path, monkeypatch, home):
    inner = tmp_path / "Library" / "LaunchAgents"
    inner.mkdir(parents=True)
    monkeypatch.setattr(settings, "device_extra_protected_paths", str(inner), raising=False)
    assert floor_for("move_file", source=str(tmp_path / "Library"), destination=str(tmp_path / "x")).code == "FLOOR_PROTECTED_PATH"
    assert floor_for("delete_file", path=str(tmp_path / "Library")).code == "FLOOR_PROTECTED_PATH"
    # reading or listing the parent is fine
    assert not floor_for("list_directory", path=str(tmp_path / "Library")).deny


def test_git_hooks_and_config_cannot_be_written(tmp_path, home):
    for rel in (".git/hooks/pre-commit", ".git/config"):
        assert floor_for("write_file", path=str(tmp_path / rel), content="x").code == "FLOOR_PROTECTED_PATH"
        assert floor_for("edit_file", path=str(tmp_path / rel), old="a", new="b").code == "FLOOR_PROTECTED_PATH"
    assert not floor_for("read_file", path=str(tmp_path / ".git" / "config")).deny
    assert not floor_for("write_file", path=str(tmp_path / ".git" / "info" / "exclude"), content="x").deny


def test_pid_of_aura_itself_is_refused():
    assert floors.check(None, None, CATALOGUE["kill_process"], match.extract(CATALOGUE["kill_process"], {"pid": os.getpid()})).code == "FLOOR_AURA_PROCESS"
    far = os.getpid() + 100000
    assert not floors.check(None, None, CATALOGUE["kill_process"], match.extract(CATALOGUE["kill_process"], {"pid": far})).deny
