"""Path spellings that need one reading: braces and wildcards in `/proc/<pid>/...`, and the Windows forms
(administrative shares of this computer, `\\\\?\\` prefixes, trailing dots and spaces, alternate streams).

Pure string work. Nothing here expands a command's wildcards or guesses what it could match: the interim command
policy asks the owner about every command instead (see wall.py).
"""

from __future__ import annotations

import ipaddress
import platform
import re
import socket

GLOB_CHARS = "*?["
_EXTGLOB = re.compile(r"[@+!?*]\(")
_BRACE = re.compile(r"\{[^{}]*(,|\.\.)[^{}]*\}")
MAX_VARIANTS = 256


def has_glob(word: str) -> bool:
    """A word the shell may expand to other words: wildcard, brace list, or extended-glob group."""
    return any(c in word for c in GLOB_CHARS) or bool(_BRACE.search(word)) or bool(_EXTGLOB.search(word))




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




_ADMIN_SHARE = re.compile(r"[A-Za-z]\$", re.IGNORECASE)


def _local_names() -> set[str]:
    names = {"localhost", "localhost.localdomain", ".", "::1", "0.0.0.0"}
    for fn in (socket.gethostname, platform.node, socket.getfqdn):
        try:
            n = (fn() or "").strip().lower().rstrip(".")
        except Exception:
            continue
        if n:
            names.add(n)
            names.add(n.split(".", 1)[0])
    return names


def _is_this_computer(server: str) -> bool:
    s = server.strip("[]").strip().lower().rstrip(".")
    if not s:
        return False
    if s in _local_names():
        return True
    if s.endswith(".ipv6-literal.net"):
        s = s[: -len(".ipv6-literal.net")].replace("-", ":").replace("s", "%")
    try:
        ip = ipaddress.ip_address(s.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


def _unc_parts(path: str) -> tuple[str, str, str] | None:
    """(server, share, rest) of a UNC path in forward-slash form, `\\\\?\\UNC\\` included; None if it is not one."""
    p = path.replace("\\", "/")
    if p[:4] in ("//?/", "//./"):
        p = p[4:]
        if p[:4].upper() == "UNC/":
            p = "//" + p[4:]
        else:
            return None
    if not p.startswith("//") or p.startswith("///"):
        return None
    parts = p[2:].split("/", 2)
    if not parts[0]:
        return None
    return parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""


def local_unc_to_drive(path: str) -> str | None:
    """`\\\\127.0.0.1\\c$\\x`, `\\\\localhost\\c$\\x`, `\\\\<this host>\\c$\\x`, `\\\\?\\UNC\\...` and `\\\\?\\C:\\x`
    as `C:/x`; None for anything else (including a share on another computer)."""
    p = path.replace("\\", "/")
    if p[:4] in ("//?/", "//./") and re.match(r"[A-Za-z]:", p[4:6]):
        return p[4:]
    unc = _unc_parts(path)
    if unc and _ADMIN_SHARE.fullmatch(unc[1]) and _is_this_computer(unc[0]):
        return unc[1][0].upper() + ":/" + unc[2]
    return None



def _clean(comp: str, windows: bool) -> str:
    """Windows ignores trailing dots and spaces in a name, and `name::$DATA` / `name:stream` is the same file."""
    if windows and comp not in (".", ".."):
        if ":" in comp and not re.fullmatch(r"[A-Za-z]:", comp):
            comp = comp.split(":", 1)[0]
        comp = comp.rstrip(". ") or comp
    return comp


def components(path: str, windows: bool = False) -> list[str]:
    path = path.replace("\\", "/")
    if windows:
        local = local_unc_to_drive(path)
        if local is not None:
            path = local
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


