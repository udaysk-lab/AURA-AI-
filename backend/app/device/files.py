"""File operations, fenced to the allowed roots.

The agent can read, write, edit, list, search, move and delete files on the
machine — but only inside the directories you allowed, and never inside a
protected path. The fence is the important part: `resolve()` turns whatever the
model wrote into an absolute real path (expanding `~`, collapsing `..`,
following symlinks on the parts that exist) and refuses anything that lands
outside the roots or inside a protected path, so there is no spelling of a path
that reaches `/etc/shadow` or `~/.ssh` from a tool call.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from app.config import settings
from app.device.runtime import DeviceUnavailable, require_enabled


class PathNotAllowed(DeviceUnavailable):
    """A path outside the allowed roots, or inside a protected one."""


@dataclass
class FileEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    modified: float


def _within(path: str, root: str) -> bool:
    """True if `path` is `root` or sits underneath it. Both absolute real paths."""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False  # different drives on Windows


def resolve(path: str, *, must_exist: bool = False) -> str:
    """Resolve a model-supplied path to a vetted absolute real path.

    Raises PathNotAllowed unless the result is inside an allowed root and
    outside every protected path. This is the only way the file tools turn a
    string into a path they will touch.
    """
    require_enabled()
    if not path or not str(path).strip():
        raise PathNotAllowed("No path was given.")

    expanded = os.path.expanduser(os.path.expandvars(str(path).strip()))
    if not os.path.isabs(expanded):
        expanded = os.path.join(os.getcwd(), expanded)

    # realpath resolves symlinks so a link inside a root can't point outside it.
    real = os.path.realpath(expanded)

    roots = settings.device_allowed_root_list
    if not any(_within(real, root) for root in roots):
        raise PathNotAllowed(
            f"{path!r} is outside the folders AURA may touch. Allowed: "
            + ", ".join(roots)
            + ". Widen DEVICE_ALLOWED_ROOTS to change this."
        )

    for protected in settings.device_protected_path_list:
        if real == protected or _within(real, protected):
            raise PathNotAllowed(
                f"{path!r} is in a protected location AURA won't touch "
                "(keys, credentials, its own config)."
            )

    if must_exist and not os.path.exists(real):
        raise PathNotAllowed(f"No such file or directory: {path}")
    return real


def _rel(path: str) -> str:
    """A tidy display path: under the home dir it shows as ~/…."""
    home = os.path.abspath(os.path.expanduser("~"))
    if _within(path, home):
        return "~" + path[len(home):]
    return path


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def read_text(path: str, max_bytes: int | None = None) -> dict:
    real = resolve(path, must_exist=True)
    if os.path.isdir(real):
        raise PathNotAllowed(f"{path} is a directory. Use list_directory.")
    limit = max_bytes or settings.device_max_read_bytes
    size = os.path.getsize(real)
    with open(real, "rb") as fh:
        raw = fh.read(limit + 1)
    truncated = len(raw) > limit
    raw = raw[:limit]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return {
        "path": _rel(real),
        "size": size,
        "truncated": truncated,
        "content": text,
    }


def list_directory(path: str = "~", show_hidden: bool = False) -> dict:
    real = resolve(path, must_exist=True)
    if not os.path.isdir(real):
        raise PathNotAllowed(f"{path} is not a directory.")
    entries: list[dict] = []
    with os.scandir(real) as it:
        for entry in it:
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                stat = entry.stat()
                entries.append(
                    {
                        "name": entry.name,
                        "path": _rel(entry.path),
                        "is_dir": entry.is_dir(),
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                    }
                )
            except OSError:
                continue
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return {"path": _rel(real), "count": len(entries), "entries": entries[:500]}


def search_files(
    query: str, root: str = "~", *, in_content: bool = False, limit: int = 50
) -> list[dict]:
    """Find files by name, or grep their text when in_content is set."""
    base = resolve(root, must_exist=True)
    needle = (query or "").lower()
    if not needle:
        return []
    hits: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(base):
        # Don't descend into protected dirs or obvious noise.
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".")
            and d not in {"node_modules", "__pycache__", "venv", ".venv", "dist", "build"}
            and _safe(os.path.join(dirpath, d))
        ]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if not _safe(full):
                continue
            if not in_content:
                if needle in name.lower():
                    hits.append({"path": _rel(full), "name": name})
            else:
                match = _grep(full, needle)
                if match:
                    hits.append({"path": _rel(full), "name": name, "match": match})
            if len(hits) >= limit:
                return hits
    return hits


def _safe(path: str) -> bool:
    try:
        resolve(path)
        return True
    except DeviceUnavailable:
        return False


def _grep(path: str, needle: str) -> str | None:
    try:
        if os.path.getsize(path) > 2_000_000:
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if needle in line.lower():
                    return line.strip()[:200]
    except OSError:
        return None
    return None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def write_text(path: str, content: str, *, append: bool = False) -> dict:
    real = resolve(path)
    os.makedirs(os.path.dirname(real) or ".", exist_ok=True)
    existed = os.path.exists(real)
    with open(real, "a" if append else "w", encoding="utf-8") as fh:
        fh.write(content)
    return {
        "path": _rel(real),
        "bytes": len(content.encode("utf-8")),
        "created": not existed,
        "appended": append,
    }


def edit_text(path: str, find: str, replace: str, *, count: int = 0) -> dict:
    """Replace exact text in a file. count=0 replaces every occurrence."""
    real = resolve(path, must_exist=True)
    with open(real, "r", encoding="utf-8", errors="replace") as fh:
        original = fh.read()
    occurrences = original.count(find)
    if occurrences == 0:
        raise DeviceUnavailable(
            "The text to replace wasn't found, so nothing was changed. "
            "Read the file first and copy the text exactly."
        )
    updated = original.replace(find, replace, count if count > 0 else -1)
    with open(real, "w", encoding="utf-8") as fh:
        fh.write(updated)
    return {
        "path": _rel(real),
        "replaced": occurrences if count == 0 else min(count, occurrences),
    }


def move_path(src: str, dst: str) -> dict:
    real_src = resolve(src, must_exist=True)
    real_dst = resolve(dst)
    os.makedirs(os.path.dirname(real_dst) or ".", exist_ok=True)
    shutil.move(real_src, real_dst)
    return {"from": _rel(real_src), "to": _rel(real_dst)}


def delete_path(path: str) -> dict:
    real = resolve(path, must_exist=True)
    if os.path.isdir(real):
        shutil.rmtree(real)
        return {"path": _rel(real), "deleted": "directory"}
    os.remove(real)
    return {"path": _rel(real), "deleted": "file"}


def make_directory(path: str) -> dict:
    real = resolve(path)
    os.makedirs(real, exist_ok=True)
    return {"path": _rel(real), "created": True}
