"""What opening a file with the system's default handler can do (the one place for the allow-list).

`open_app` with a file target hands the file to `os.startfile`, `open` or `xdg-open`. For a document or a media
file that shows it. For a script, a program, a shortcut, an installer or a macro document it can run it. The gate
decides on the canonical real path (links followed) with `classify`, and the handler classifies the same path again
with the same function before it opens exactly that path, so the decision and the action cannot disagree.

Only the types in `OPEN_ALLOWED` follow the mode and the rules. Everything else, and anything whose content does
not look like its name, needs the owner every time (reason code UNCHECKED_OPEN).
"""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass

TEXT = frozenset({".txt", ".md", ".markdown", ".csv", ".tsv"})
PDF = frozenset({".pdf"})
IMAGES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".heif"})
AUDIO = frozenset({".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac"})
VIDEO = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpg", ".mpeg"})
# Office and OpenDocument formats that cannot carry macros by name (.docm, .xlsm, .pptm and the old binary
# .doc/.xls/.ppt are not here). A zip of these types must be the family its name says and is looked into for macro parts.
OOXML = frozenset({".docx", ".xlsx", ".pptx"})
ODF = frozenset({".odt", ".ods", ".odp", ".odg"})

# The whole allow-list. tests/test_r2b_open_files.py pins it; change it only on purpose.
OPEN_ALLOWED = frozenset(TEXT | PDF | IMAGES | AUDIO | VIDEO | OOXML | ODF)

# Folders that are programs when opened (macOS bundles and the like).
BUNDLE_SUFFIXES = (".app", ".workflow", ".action", ".saver", ".prefpane", ".bundle", ".pkg", ".mpkg", ".command")

_EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"#!", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe",
                     b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",), ".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8",), ".jpeg": (b"\xff\xd8",), ".gif": (b"GIF8",),
}
_ZIP = b"PK\x03\x04"


@dataclass(frozen=True)
class Kind:
    kind: str  # folder | document | unchecked | missing
    ext: str = ""
    why: str = ""


def extension(path: str) -> str:
    """The lower-case extension as the system's handler sees it (trailing dots and spaces ignored on Windows)."""
    name = os.path.basename(path.rstrip("/\\"))
    if os.name == "nt":
        name = name.rstrip(". ")
    return os.path.splitext(name)[1].lower()


def _head(path: str, n: int = 16) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return None


_ODF_MIME = b"application/vnd.oasis.opendocument."
_MEMBER_LIMIT = 5000


def _zip_problem(path: str, ext: str) -> str:
    """"" when this zip is the document its name says and holds no macros or scripts, else why not.

    The name says which family the content must belong to, and the content decides how a program that sniffs files
    (rather than trusting names) will treat it: an Office Open XML name needs `[Content_Types].xml` and no `mimetype`
    member; an OpenDocument name needs a `mimetype` member that starts with the OpenDocument type and no
    `[Content_Types].xml`. Every macro and script check runs for both families."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if len(names) > _MEMBER_LIMIT:
                return "it has too many parts to check"
            low = [n.lower() for n in names]
            has_types = "[content_types].xml" in low
            has_mime = "mimetype" in low
            if ext in OOXML:
                if not has_types or has_mime:
                    return "its content is not what its name says"
            else:
                if has_types or not has_mime:
                    return "its content is not what its name says"
                with z.open(names[low.index("mimetype")]) as fh:
                    if not fh.read(len(_ODF_MIME)).lower().startswith(_ODF_MIME):
                        return "its content is not what its name says"
            for n in low:
                if n.endswith("vbaproject.bin") or n.startswith(("basic/", "scripts/", "macros/")):
                    return "it holds macros or scripts"
            return ""
    except (OSError, zipfile.BadZipFile, ValueError, RuntimeError, NotImplementedError, EOFError):
        return "it cannot be checked"


def classify(real: str) -> Kind:
    """Judge one canonical real path. `real` must already have its links followed."""
    try:
        exists = os.path.exists(real)
    except (OSError, ValueError):
        return Kind("unchecked", why="unreadable")
    if not exists:
        return Kind("missing")
    ext = extension(real)
    if os.path.isdir(real):
        if ext in BUNDLE_SUFFIXES or os.path.basename(real.rstrip("/\\")).lower().endswith(BUNDLE_SUFFIXES):
            return Kind("unchecked", ext, "an application bundle")
        return Kind("folder", ext)
    if not os.path.isfile(real):
        return Kind("unchecked", ext, "not an ordinary file")
    if ext not in OPEN_ALLOWED:
        return Kind("unchecked", ext, "a type that is not on the list of documents and media")
    if os.name != "nt" and os.access(real, os.X_OK):
        return Kind("unchecked", ext, "marked as a program")
    head = _head(real, 16)
    if head is None:
        return Kind("unchecked", ext, "unreadable")
    if head.startswith(_EXECUTABLE_MAGIC):
        return Kind("unchecked", ext, "its content is a program or script, not what its name says")
    if ext in _MAGIC and not head.startswith(_MAGIC[ext]):
        return Kind("unchecked", ext, "its content is not what its name says")
    if ext in OOXML | ODF:
        if not head.startswith(_ZIP):
            return Kind("unchecked", ext, "its content is not what its name says")
        problem = _zip_problem(real, ext)
        if problem:
            return Kind("unchecked", ext, problem)
    return Kind("document", ext)


def needs_owner(kind: Kind) -> bool:
    return kind.kind == "unchecked"
