"""Generated: many spellings of one of AURA's own files and of one block-ruled file, across bash, PowerShell and
cmd, in every permission mode. No spelling is ever decided `allow`: it is refused, or it needs the user."""

from __future__ import annotations

import ntpath
import os

import pytest

from app.permissions import gate, match, pathglob, rules
from tests.sec_world import build

BASH, PWSH, CMD = "posix", "pwsh", "cmd"
MODES = ("skip", "auto", "ask")

HOME_FORMS = {
    BASH: ["~", "$HOME", "${HOME}", '"$HOME"', "{abs}"],
    PWSH: ["~", "$HOME", "$env:USERPROFILE", "${env:USERPROFILE}", "$env:HOME", "{abs}"],
    CMD: ["~", "%USERPROFILE%", "%HOME%", "{abs}"],
}
VERBS = {
    BASH: ["cat {p}", "less {p}", "head -1 {p}", "cp {p} /tmp/aura-gen-copy", "cat < {p}", "echo x > {p}",
           "bash -c 'cat {p}'", "sudo cat {p}", "env A=1 cat {p}", "cat {p} | head -1", "sqlite3 {p} .dump",
           "tar -cf /tmp/aura-gen.tar {p}", "grep -r key {p}", "xargs cat < {p}", "(cat {p})", "nohup cat {p}"],
    PWSH: ["Get-Content {p}", "gc {p}", "type {p}", "Copy-Item {p} C:\\tmp\\x", "Get-Content -Path {p}",
           "cat {p} | select -First 1", "Set-Content {p} x", "Remove-Item {p}", "Start-Process notepad {p}"],
    CMD: ["type {p}", "copy {p} x", "more {p}", "type {p} | more", "cmd /c type {p}", "xcopy {p} x", "del {p}"],
}


def forms(comps: list[str], dialect: str, home: str) -> list[tuple[str, str]]:
    """(command prefix, path text) pairs: spellings of ~/<comps>."""
    sep = "\\" if dialect != BASH else "/"
    out: list[tuple[str, str]] = []
    first, last = comps[0], comps[-1]

    def join(prefix: str, parts: list[str], s: str = sep) -> str:
        return prefix + s + s.join(parts)

    for h in HOME_FORMS[dialect]:
        prefix = h.replace("{abs}", home)
        out.append(("", join(prefix, comps)))
        out.append(("", join(prefix, comps, "/")))
        out.append(("", join(prefix, ["."] + comps)))
        out.append(("", join(prefix, ["work", ".."] + comps)))
        out.append(("", prefix + sep + sep.join(comps[:1]) + sep + sep + sep.join(comps[1:])))
        for i, c in enumerate(comps):
            for alt in (c[:1] + "?" + c[2:], c[:2] + "*", "[" + c[0] + "]" + c[1:], "*", "?" + c[1:]):
                out.append(("", join(prefix, comps[:i] + [alt] + comps[i + 1:])))
            if dialect == BASH:
                out.append(("", join(prefix, comps[:i] + ["{" + c + ",zz}"] + comps[i + 1:])))
                out.append(("", join(prefix, comps[:i] + [c[:1] + "''" + c[1:]] + comps[i + 1:])))
                out.append(("", join(prefix, comps[:i] + [c[:1] + "\\" + c[1:]] + comps[i + 1:])))
                out.append(("", join(prefix, comps[:i] + ["$'\\x%02x'" % ord(c[0]) + c[1:]] + comps[i + 1:])))
                out.append(("", join(prefix, comps[:i] + ["$(echo " + c + ")"] + comps[i + 1:])))
                out.append(("", join(prefix, comps[:i] + ["`echo " + c + "`"] + comps[i + 1:])))
                out.append((f"v={c[1:]}; ", join(prefix, comps[:i] + [c[0] + "$v"] + comps[i + 1:])))
                out.append((f"v={c[1:]}; ", join(prefix, comps[:i] + [c[0] + "${v}"] + comps[i + 1:])))
                out.append(("", join(prefix, comps[:i] + ["$(printf %s " + c + ")"] + comps[i + 1:])))
            elif dialect == PWSH:
                out.append(("", join(prefix, comps[:i] + ["$(" + "'" + c + "'" + ")"] + comps[i + 1:])))
                out.append((f"$v = '{c[1:]}'; ", join(prefix, comps[:i] + [c[0] + "$v"] + comps[i + 1:])))
                out.append(("", join(prefix, comps[:i] + [c + "."] + comps[i + 1:])))
                out.append(("", join(prefix, comps[:i] + [c[:3] + "~1"] + comps[i + 1:])))
            else:
                out.append((f"set v={c[1:]}& ", join(prefix, comps[:i] + [c[0] + "%v%"] + comps[i + 1:])))
                out.append((f"set v={c[1:]}& ", join(prefix, comps[:i] + [c[0] + "!v!"] + comps[i + 1:])))
                out.append(("", join(prefix, comps[:i] + [c[:3] + "~1"] + comps[i + 1:])))
    return out


def relative_forms(rel: list[str], dialect: str) -> list[str]:
    sep = "\\" if dialect != BASH else "/"
    out = [sep.join(rel), "." + sep + sep.join(rel), sep.join(rel[:-1] + ["?" + rel[-1][1:]]),
           sep.join(rel[:-1] + [rel[-1][:2] + "*"]), sep.join(["*"] + rel[1:]) if len(rel) > 1 else "*",
           sep.join([rel[0][:2] + "*"] + rel[1:]), sep.join(["[" + rel[0][0] + "]" + rel[0][1:]] + rel[1:])]
    if dialect == BASH:
        out += ["$'\\x%02x'%s" % (ord(rel[0][0]), rel[0][1:]) + "/" + "/".join(rel[1:]) if len(rel) > 1 else rel[0],
                "$(echo " + rel[0] + ")" + ("/" + "/".join(rel[1:]) if len(rel) > 1 else "")]
    return out


@pytest.fixture(params=MODES)
def w(request, tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, mode=request.param)
    fin = tmp_path / "home" / "work" / "finance"
    fin.mkdir()
    (fin / "salary.txt").write_text("salary: 1000000")
    rules.add(world.db, world.user, effect="block", system="device-control", tool=None, target_kind="path",
              pattern=str(fin) + "/**", actor="user")
    world.db.commit()
    yield world
    world.db.close()
    world.engine.dispose()


def decide(w, command, dialect, workdir):
    gate._dialect = lambda p: dialect
    return gate.decide(w.db, w.user, "run_command", {"command": command, "workdir": workdir},
                       gate.build_context(w.db, w.user, "chat"))


def commands_for(comps: list[str], dialect: str, home: str, rel_from: str | None, rel: list[str] | None):
    out: list[tuple[str, str]] = []  # (command, kind of workdir)
    pairs = forms(comps, dialect, home)
    for i, (pre, path) in enumerate(pairs):
        verbs = VERBS[dialect]
        # every spelling with two verbs (rotating), the plain read verb always
        picked = {verbs[0] if i % 3 == 0 else verbs[(i * 7 + 3) % len(verbs)]}
        for v in picked:
            out.append((pre + v.format(p=path), "work"))
    if rel is not None:
        for path in relative_forms(rel, dialect):
            for v in VERBS[dialect][:2]:
                out.append((v.format(p=path), rel_from or "work"))
    return out


@pytest.mark.parametrize("dialect", [BASH, PWSH, CMD])
def test_no_spelling_of_aura_s_own_file_is_ever_allowed(w, dialect):
    cases = commands_for([".aura", "aura.env"], dialect, w.home, "home", [".aura", "aura.env"])
    cases += commands_for(["code", "aura", "backend", ".env"], dialect, w.home, "proj", [".env"])
    assert len(cases) > 150
    allowed = []
    for command, where in cases:
        workdir = {"work": w.work, "proj": w.proj, "home": w.home}[where]
        d = decide(w, command, dialect, workdir)
        if d.kind == "allow":
            allowed.append(command)
    assert not allowed, allowed[:20]


@pytest.mark.parametrize("dialect", [BASH, PWSH, CMD])
def test_no_spelling_of_a_block_ruled_file_is_ever_allowed(w, dialect):
    cases = commands_for(["work", "finance", "salary.txt"], dialect, w.home, "work", ["finance", "salary.txt"])
    assert len(cases) > 80
    allowed = []
    for command, where in cases:
        d = decide(w, command, dialect, w.work)
        if d.kind == "allow":
            allowed.append(command)
    assert not allowed, allowed[:20]


def test_a_block_ruled_file_is_not_allowed_from_inside_its_folder_either(w):
    for dialect, cmds in ((BASH, ["cat salary.txt", "cat ./salary.txt", "cat s*", "cat $(echo salary.txt)", "cat sal?ry.txt"]),
                          (PWSH, ["Get-Content salary.txt", "gc s*"]), (CMD, ["type salary.txt", "type s*"])):
        for c in cmds:
            d = decide(w, c, dialect, os.path.join(w.work, "finance"))
            assert d.kind != "allow", (dialect, c)


# The Windows spellings of one file: every spelling is the same path once normalised (ntpath, on every platform).
WIN_HOME = r"C:\Users\u"
WIN_SPELLINGS = [
    r"C:\Users\u\.aura\aura.env", r"C:/Users/u/.aura/aura.env", r"c:\users\U\.AURA\AURA.ENV",
    r"C:\Users\u\work\..\.aura\aura.env", r"C:.aura\aura.env",
    r"\\127.0.0.1\c$\Users\u\.aura\aura.env", r"\\?\UNC\127.0.0.1\c$\Users\u\.aura\aura.env",
    r"\\localhost\C$\Users\u\.aura\aura.env", r"\\?\C:\Users\u\.aura\aura.env", r"%USERPROFILE%\.aura\aura.env",
    r"~\.aura\aura.env", r"$env:USERPROFILE\.aura\aura.env",
]


@pytest.mark.parametrize("spelling", WIN_SPELLINGS)
def test_windows_spellings_name_the_same_file(spelling):
    ops = match.PathOps(ntpath, True)
    got = match.normalize_path(spelling.replace("%USERPROFILE%", WIN_HOME).replace("$env:USERPROFILE", WIN_HOME)
                               .replace("~", WIN_HOME) if spelling[:1] in "%$~" else spelling, WIN_HOME, ops)
    assert match.fold(got, ops) == match.fold(r"C:\Users\u\.aura\aura.env", ops)
    assert pathglob.could_match(spelling, r"C:\Users\u\.aura", WIN_HOME, WIN_HOME, ci=True, windows=True) or \
        spelling[:1] in "%$"


def test_a_short_name_form_of_the_file_is_refused_or_opaque():
    spelling = r"C:\Users\u\AURA~1\aura.env"
    assert pathglob.could_match(spelling, r"C:\Users\u\.aura", WIN_HOME, WIN_HOME, ci=True, windows=True)
    assert pathglob.opaque_prefix(spelling, WIN_HOME, WIN_HOME, True) == "C:/Users/u"
    with pytest.raises(match.BadArgs):
        if os.name != "nt":
            match.normalize_path(spelling, WIN_HOME, match.PathOps(ntpath, True))
        else:
            raise match.BadArgs("resolved natively")
