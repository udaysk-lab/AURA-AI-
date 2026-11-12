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
