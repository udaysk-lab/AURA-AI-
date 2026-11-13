"""Glob, brace and tilde forms in the paths of a shell command (review finding F4).

A shell turns `~/.au[r]a/aur[a].env` into `/home/u/.aura/aura.env` before the program runs, so a
check that compares the text of a command with AURA's own paths is blind to it. The helpers here
answer the two questions the floors need, without running anything:

* `existing_matches(...)`: which paths that really exist could this word expand to?
* `could_match(...)`: could this word expand to a given path (which need not exist yet)?

Both are deliberately generous. A word counts as a pattern if it holds `* ? [`, a brace list, or an
extended-glob group; a group in parentheses stands for "anything"; unknown shell options are assumed
to be the ones that match the most (a leading wildcard matches a leading dot when the command
mentions `dotglob`, `GLOBIGNORE` or `setopt`). The comparison is on path components, with Windows
and macOS case rules, trailing dots and spaces dropped (Windows ignores them) and both separators
accepted, so one spelling of a path cannot be judged differently from another.

Pure string work except `existing_matches`, which lists directories (a bounded amount of them).
"""

from __future__ import annotations

import fnmatch
import os
import re
from typing import Iterable

POSIX, PWSH, CMD = "posix", "pwsh", "cmd"

GLOB_CHARS = "*?["
_EXTGLOB = re.compile(r"[@+!?*]\(")
_BRACE = re.compile(r"\{[^{}]*(,|\.\.)[^{}]*\}")
_SEP = re.compile(r"[\\/]+")
_DOT_OPTIONS = re.compile(r"dotglob|globignore|setopt|glob_dots|nullglob.*\.", re.IGNORECASE)
MAX_VARIANTS = 256
MAX_VISITED = 4000


def has_glob(word: str) -> bool:
    """A word the shell may expand to other words: wildcard, brace list, or extended-glob group."""
    return any(c in word for c in GLOB_CHARS) or bool(_BRACE.search(word)) or bool(_EXTGLOB.search(word))


def dotglob_on(command: str) -> bool:
    """The command changes how wildcards treat a leading dot (so `*` may match `.aura`)."""
    return bool(_DOT_OPTIONS.search(command or ""))


# ---------------------------------------------------------------------------
# Braces
# ---------------------------------------------------------------------------


def _split_top(body: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _range(body: str) -> list[str] | None:
    m = re.fullmatch(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?", body) or re.fullmatch(r"([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?", body)
    if not m:
        return None
    a, b, step = m.group(1), m.group(2), int(m.group(3) or 1) or 1
    if a.lstrip("-").isdigit():
        lo, hi = int(a), int(b)
        width = len(a.lstrip("-")) if a.lstrip("-").startswith("0") and len(a.lstrip("-")) > 1 else 0
        seq = range(lo, hi + 1, abs(step)) if lo <= hi else range(lo, hi - 1, -abs(step))
        if len(seq) > MAX_VARIANTS:
            return None
        return [str(n).zfill(width) if width else str(n) for n in seq]
    lo, hi = ord(a), ord(b)
    seq = range(lo, hi + 1, abs(step)) if lo <= hi else range(lo, hi - 1, -abs(step))
    return [chr(n) for n in seq]


def expand_braces(word: str) -> list[str] | None:
    """`a{b,c}d` -> [`abd`, `acd`]; numeric and letter ranges too. None if there are too many results."""
    out = [word]
    for _ in range(8):  # nested lists are expanded one level per pass
        nxt: list[str] = []
        changed = False
        for w in out:
            m = _BRACE.search(w)
            if not m:
                nxt.append(w)
                continue
            changed = True
            body = m.group(0)[1:-1]
            alts = _range(body) if ".." in body and "," not in body else None
            if alts is None:
                alts = _split_top(body) if "," in body else None
            if alts is None:
                return None
            for alt in alts:
                nxt.append(w[: m.start()] + alt + w[m.end():])
            if len(nxt) > MAX_VARIANTS:
                return None
        out = nxt
        if not changed:
            return out
    return out if len(out) <= MAX_VARIANTS else None


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def _clean(comp: str, windows: bool) -> str:
    """Windows ignores trailing dots and spaces in a name, and `name::$DATA` / `name:stream` is the same file."""
    if windows and comp not in (".", ".."):
        if ":" in comp and not re.fullmatch(r"[A-Za-z]:", comp):
            comp = comp.split(":", 1)[0]
        comp = comp.rstrip(". ") or comp
    return comp


def _fold(text: str, ci: bool) -> str:
    return os.path.normcase(text).casefold() if ci else text


def _strip_groups(comp: str) -> str:
    """Parenthesised groups (extended globs, subshell-looking text) stand for anything."""
    out, depth = [], 0
    for ch in comp:
        if ch == "(":
            if depth == 0:
                if out and out[-1] in "@+!?*":
                    out.pop()
                out.append("*")
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def components(path: str, windows: bool = False) -> list[str]:
    path = path.replace("\\", "/")
    if windows and path.startswith("//?/"):
        path = path[4:]
        if path[:4].upper() == "UNC/":
            path = "//" + path[4:]
    elif windows and path.startswith("//./"):
        path = path[4:]
    parts = [p for p in path.split("/")]
    lead = ""
    if path.startswith("//") and windows:
        lead = "//"  # a UNC path: the server and share are components of their own
    comps: list[str] = []
    for p in parts:
        if p in ("", "."):
            continue
        if p == "..":
            if comps and comps[-1] not in ("..",) and not re.fullmatch(r"[A-Za-z]:", comps[-1]):
                comps.pop()
            continue
        comps.append(_clean(p, windows))
    if path.startswith("/") and not lead:
        comps.insert(0, "/")
    elif lead:
        comps.insert(0, "//")
    return comps


def _home_forms(word: str, home: str) -> str:
    h = home.replace("\\", "/")
    text = word
    low = text.lower()
    for form in ("$home", "${home}", "%userprofile%", "%home%", "$env:userprofile", "$env:home", "$homepath"):
        if low.startswith(form) and (len(text) == len(form) or text[len(form)] in "/\\"):
            return h + text[len(form):]
    if text == "~" or text.startswith(("~/", "~\\")):
        return h + text[1:]
    return text


def absolute_components(word: str, base: str, home: str, windows: bool = False) -> list[str]:
    """The components of `word` as an absolute path (relative words start at `base`)."""
    text = _home_forms(word, home).replace("\\", "/")
    is_abs = text.startswith("/") or bool(re.match(r"^[A-Za-z]:/", text)) or text.startswith("//")
    if not is_abs and re.match(r"^[A-Za-z]:", text) and windows:
        is_abs = True  # `C:name` is relative to a drive's directory; treat as the drive root
        text = text[:2] + "/" + text[2:]
    if not is_abs:
        text = base.replace("\\", "/").rstrip("/") + "/" + text
    return components(text, windows)


# ---------------------------------------------------------------------------
# Could this word name that path?
# ---------------------------------------------------------------------------


def _comp_could_match(pat: str, name: str, ci: bool, dot_ok: bool) -> bool:
    if pat == "**":
        return True
    p = _strip_groups(pat)
    if not any(c in p for c in GLOB_CHARS):
        return _fold(p, ci) == _fold(name, ci)
    if name.startswith(".") and not p.startswith(".") and not dot_ok:
        return False
    return fnmatch.fnmatchcase(_fold(name, ci), _fold(p, ci))


def _match_prefix(pcomps: list[str], tcomps: list[str], ci: bool, dot_ok: bool) -> bool:
    """Do the pattern's components name `tcomps` or something inside it?"""
    if not tcomps:
        return True
    if not pcomps:
        return False
    head, rest = pcomps[0], pcomps[1:]
    if head == "**":
        if not rest:
            return True  # a trailing ** is anything inside
        # zero directories, or one more directory consumed
        return _match_prefix(rest, tcomps, ci, dot_ok) or (
            len(tcomps) > 1 and _match_prefix(pcomps, tcomps[1:], ci, dot_ok))
    if not _comp_could_match(head, tcomps[0], ci, dot_ok):
        return False
    if len(tcomps) == 1:
        return True
    return _match_prefix(rest, tcomps[1:], ci, dot_ok)


def could_match(word: str, target: str, base: str, home: str, *, ci: bool = True, windows: bool = False,
                dot_ok: bool = False) -> bool:
    """Could `word` (a path word with wildcards) expand to `target` or to something inside it?

    `target` is a real path. Every brace variant of the word is tried."""
    variants = expand_braces(word)
    if variants is None:
        variants = ["*"]  # too many to list: treat as "anything" (the generous reading)
    tcomps = components(target, windows)
    for v in variants:
        pcomps = absolute_components(v, base, home, windows)
        if _match_prefix(pcomps, tcomps, ci, dot_ok):
            return True
    return False


# ---------------------------------------------------------------------------
# What does it match today?
# ---------------------------------------------------------------------------


def existing_matches(word: str, base: str, home: str, *, ci: bool = True, windows: bool = False,
                     dot_ok: bool = False, limit: int = 200) -> list[str]:
    """Real paths that exist now and that `word` expands to (every brace variant, bounded work)."""
    variants = expand_braces(word) or []
    found: list[str] = []
    visited = 0
    for v in variants:
        comps = absolute_components(v, base, home, windows)
        if not comps:
            continue
        if comps[0] == "/":
            roots = ["/"] if os.sep == "/" else [os.path.abspath(os.sep)]
            rest = comps[1:]
        elif comps[0] == "//":
            roots, rest = ["\\\\"], comps[1:]
        elif re.fullmatch(r"[A-Za-z]:", comps[0]):
            roots, rest = [comps[0] + os.sep], comps[1:]
        else:
            roots, rest = [os.getcwd()], comps
        level = roots
        for i, pat in enumerate(rest):
            nxt: list[str] = []
            plain = not any(c in _strip_groups(pat) for c in GLOB_CHARS) and pat != "**"
            for parent in level:
                if plain:
                    cand = os.path.join(parent, _strip_groups(pat))
                    if os.path.lexists(cand):
                        nxt.append(cand)
                    continue
                try:
                    with os.scandir(parent) as it:
                        for entry in it:
                            visited += 1
                            if visited > MAX_VISITED:
                                break
                            if _comp_could_match(pat, entry.name, ci, dot_ok):
                                nxt.append(entry.path)
                except OSError:
                    continue
            level = nxt[:limit * 4]
            if not level:
                break
        found.extend(level)
        if len(found) >= limit or visited > MAX_VISITED:
            break
    out: list[str] = []
    for p in found[:limit]:
        real = os.path.realpath(p)
        if real not in out:
            out.append(real)
    return out


def words_of(text: str) -> list[str]:
    """Rough words of a command line for pattern checks: split on white space and statement
    separators but not on parentheses or braces, quotes and escapes removed. Over-inclusive."""
    cleaned = re.sub(r"[\"'`^]", "", text)
    cleaned = re.sub(r"\\(?=[^\\/\s])", "", cleaned)
    out: list[str] = []
    for chunk in re.split(r"[\s;|&<>]+", cleaned):
        if chunk and chunk not in out:
            out.append(chunk)
    return out


def unique(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for i in items:
        if i not in out:
            out.append(i)
    return out
