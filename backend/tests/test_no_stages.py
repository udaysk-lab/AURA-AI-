"""The relationship-stage concept is gone. Fail if it comes back.

Scans backend/app, backend/*.py and frontend/ (not node_modules, .next, out,
and not the two migration revisions that are history).
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
FRONTEND = BACKEND.parent / "frontend"

PATTERN = re.compile(
    r"stranger|acquaintance|chief_of_staff|chief of staff|compute_stage|refresh_stage"
    r"|progress_to_next|record_interaction|stage_label|stage_blurb|days_together"
    r"|hatched_at|\bhatch\b",
    re.IGNORECASE,
)

SKIP_DIRS = {"node_modules", ".next", "out", "__pycache__", ".git"}
SKIP_FILES = {
    "backend/alembic/versions/0001_initial_schema.py",  # history: the 1.0.0 schema
    "backend/alembic/versions/0002_legacy_defaults.py",  # history: gives defaults to the old columns
}
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".css", ".json", ".md", ".html", ".txt"}

# path (relative to the repo root) -> reason a match is not the stage concept.
ALLOWLIST = {
    # "Killing a stranger's process": a pid that may belong to someone else.
    "backend/app/device/shell.py": "stranger's process",
    # The 1.0.0 column names, kept as data until the contract revision drops them.
    "backend/app/migrate_legacy.py": "hatched_at",
    "backend/app/migration_ops.py": "hatched_at",
}


def _files():
    roots = [BACKEND / "app", FRONTEND]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and not (SKIP_DIRS & set(path.parts)):
                yield path
    yield from BACKEND.glob("*.py")


def test_no_stage_concept_left():
    repo = BACKEND.parent
    found = []
    for path in _files():
        rel = path.relative_to(repo).as_posix()
        if rel in SKIP_FILES or path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        allowed = ALLOWLIST.get(rel)
        for n, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                if allowed and allowed.lower() in line.lower():
                    continue
                found.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not found, "stage concept found:\n" + "\n".join(found)
