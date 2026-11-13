"""Preference memory: how the owner wants each kind of work done.

Spec 20-taskloop-memory.md section 10. A preference is one imperative line keyed by task type
(or `general`), optionally scoped to a system. It is captured only from the signed-in owner's
own chat messages, scrubbed of secrets, superseded when a newer line says the same thing, and
retrieved for the task type at hand before the model acts.

Capture paths (all end in `add`):
  1. a statement in the owner's message ("always ...", "from now on ...")
  2. a correction of the previous task ("no, that's wrong ...")
  3. an optional model rewrite of 1 and 2 (real providers only; failure keeps the plain text)
  4. the `aura_note_preference` tool, checked against the owner's own words
  5. settings, skill teaching, and a down-vote note
Nothing here reads tool results, emails, files, pages or channel text: the only text it is
given is `owner_message`, and only when the trigger is `chat`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.prompts import PREFERENCE_EXTRACTOR
from app.llm import get_provider
from app.models import Conversation, Message, Preference, User, UserSettings, WorkItem, utcnow
from app.services import memory as memory_service
from app.services import redaction, tasktypes

log = logging.getLogger("aura.preferences")

TEXT_MAX = 300
EXCERPT_MAX = 300
BLOCK_LINES = 12
BLOCK_CHARS = 1800
CORRECTION_WINDOW_HOURS = 24
MAX_STATEMENTS_PER_MESSAGE = 5
CURRENT_FOR_EXTRACTOR = 12

SOURCES = ("stated", "correction")
ORIGINS = ("chat", "feedback", "settings", "skill_teach", "migrated")

# 10.1 path 1
_STATEMENT = re.compile(
    r"\b(always|never|from now on|going forward|in future|next time|i prefer|i'd prefer|i'd rather|"
    r"please don't|don't ever|stop (?:doing|using|sending|adding))\b",
    re.I,
)
_QUESTION_START = re.compile(r"^(?:do|does|did|can|could|would|will|is|are|have you|what|why|how|when)(?![\w'])", re.I)
_LEADING_FILLER = re.compile(r"^(?:(?:ok|okay|so|also|and|please|aura)\b[\s,:;\-]*)+", re.I)
_TRAILING_PUNCT = re.compile(r"[\s.!,;:\-]+$")
# 10.1 path 2
_CORRECTION = re.compile(
    r"\b(?:no,|not like that|that's wrong|that is wrong|wrong again|i said|i told you|actually,? )", re.I
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

def render_extractor(message: str, types: str, systems: str, current: str) -> str:
    """Fill the template without str.format: its JSON example is full of braces."""
    out = PREFERENCE_EXTRACTOR
    for key, value in (("{types}", types), ("{systems}", systems), ("{current}", current), ("{message}", message)):
        out = out.replace(key, value)
    return out


@dataclass
class Captured:
    """What `add` did with one line."""

    preference: Preference
    created: bool  # False: an existing line said the same thing and was kept
    superseded_ids: list[str] = field(default_factory=list)

    @property
    def superseded_id(self) -> str | None:
        return self.superseded_ids[0] if self.superseded_ids else None

    def event(self) -> dict:
        """The `preference_noted` stream event body."""
        p = self.preference
        return {"type": "preference_noted", "id": p.id, "text": p.text, "task_type": p.task_type,
                "superseded_id": self.superseded_id}


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'")).strip().lower()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split((text or "").replace("’", "'")) if s.strip()]


def _clean(sentence: str) -> str:
    out = _LEADING_FILLER.sub("", sentence.strip()).strip()
    return _TRAILING_PUNCT.sub("", out).strip()


def statements(message: str) -> list[tuple[str, str]]:
    """(preference text, original sentence) for each sentence of the message that states a rule."""
    found: list[tuple[str, str]] = []
    for sentence in _sentences(message):
        if sentence.endswith("?"):
            continue
        text = _clean(sentence)
        if not text or _QUESTION_START.match(text) or not _STATEMENT.search(text):
            continue
        found.append((text[:TEXT_MAX], sentence))
        if len(found) >= MAX_STATEMENTS_PER_MESSAGE:
            break
    return found


def looks_like_correction(message: str) -> bool:
    return bool(_CORRECTION.search((message or "").replace("’", "'")))


def _normalise_type(value: str | None) -> str:
    return tasktypes.normalise(value, allow_general=True)


def _enabled(db: Session, user: User, conversation: Conversation | None) -> bool:
    """Capture is allowed: memory is not paused and this chat has memory on."""
    if conversation is not None and conversation.memory_off:
        return False
    settings = db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).first()
    return not (settings is not None and settings.memory_paused)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def add(
    db: Session,
    user: User,
    text: str,
    task_type: str,
    source: str,
    origin: str,
    *,
    system_scope: str = "",
    origin_message_id: str | None = None,
    origin_excerpt: str = "",
    origin_work_item_id: str | None = None,
) -> Captured:
    """Store one line, applying the conflict rules of section 10.3.

    Does not check the pause / memory-off switches: those guard capture from chat
    (`capture`, `note_from_tool`), not the owner editing the list in Settings.
    Raises ValueError for empty text or an unknown source / origin.
    """
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    if origin not in ORIGINS:
        raise ValueError(f"origin must be one of {ORIGINS}")
    text = redaction.scrub(db, user.id, (text or "").strip())
    text = re.sub(r"\s+", " ", text)[:TEXT_MAX].strip()
    if not text:
        raise ValueError("A preference needs some text")
    task_type = _normalise_type(task_type)
    scope = (system_scope or "").strip()[:60]
    new_tokens = memory_service.tokens(text)

    active = db.scalars(
        select(Preference).where(
            Preference.user_id == user.id,
            Preference.active.is_(True),
            Preference.task_type == task_type,
            Preference.system_scope == scope,
        )
    ).all()
    to_supersede: list[Preference] = []
    for old in active:
        score = _jaccard(new_tokens, memory_service.tokens(old.text))
        if score >= 0.9:
            old.updated_at = utcnow()  # said before: keep the old line, note it was repeated
            db.commit()
            return Captured(preference=old, created=False)
        if score >= 0.5:
            to_supersede.append(old)

    pref = Preference(
        user_id=user.id, task_type=task_type, system_scope=scope, text=text, source=source, origin=origin,
        origin_message_id=origin_message_id,
        origin_excerpt=redaction.scrub(db, user.id, (origin_excerpt or "").strip())[:EXCERPT_MAX],
        origin_work_item_id=origin_work_item_id, active=True,
    )
    db.add(pref)
    db.flush()
    for old in to_supersede:
        old.active = False
        old.superseded_by = pref.id
    db.commit()
    db.refresh(pref)
    return Captured(preference=pref, created=True, superseded_ids=[o.id for o in to_supersede])


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def supersede(db: Session, user_id: str, ids: list[str], by: Preference) -> list[str]:
    """Deactivate the named active lines of this user (never `by` itself); returns those changed."""
    changed: list[str] = []
    for pid in ids:
        old = db.get(Preference, pid)
        if old is None or old.user_id != user_id or not old.active or old.id == by.id:
            continue
        old.active = False
        old.superseded_by = by.id
        changed.append(old.id)
    if changed:
        db.commit()
    return changed


# ---------------------------------------------------------------------------
# Capture from a chat turn (paths 1 to 3)
# ---------------------------------------------------------------------------


def _last_assistant_item(db: Session, user: User, conversation: Conversation | None) -> WorkItem | None:
    """The work item the conversation's latest assistant message reports, if it finished recently."""
    if conversation is None:
        return None
    last = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
    ).first()
    item_id = (last.meta or {}).get("work_item_id") if last else None
    if not item_id:
        return None
    item = db.get(WorkItem, item_id)
    if item is None or item.user_id != user.id or item.finished_at is None:
        return None
    finished = item.finished_at if item.finished_at.tzinfo else item.finished_at.replace(tzinfo=utcnow().tzinfo)
    age = (utcnow() - finished).total_seconds()
    return item if 0 <= age <= CORRECTION_WINDOW_HOURS * 3600 else None


def capture(
    db: Session,
    user: User,
    conversation: Conversation | None,
    owner_message: str,
    trigger: str = "chat",
    turn_type: str = "other",
    *,
    systems: set[str] | None = None,
) -> list[Captured]:
    """Capture what the owner's message states or corrects. Returns the lines noted.

    Only a `chat` trigger with learning allowed does anything. `owner_message` is the signed-in
    owner's own text of this turn and nothing else.
    """
    if trigger != "chat" or not (owner_message or "").strip():
        return []
    if not _enabled(db, user, conversation):
        return []
    message = owner_message.strip()[:4000]
    noted: list[Captured] = []

    candidates: list[dict] = []
    for text, sentence in statements(message):
        task_type = tasktypes.classify(sentence)
        if task_type == "other":
            task_type = turn_type if turn_type and turn_type != "other" else tasktypes.GENERAL
        candidates.append({"text": text, "type": task_type, "excerpt": sentence, "source": "stated",
                           "item": None, "scope": "", "replaces": []})

    item = _last_assistant_item(db, user, conversation) if looks_like_correction(message) else None
    if item is not None:
        # Whatever else it says, the owner is unhappy with that task.
        if not (item.user_feedback or {}).get("rating"):
            item.user_feedback = {"rating": "down", "note": redaction.scrub(db, user.id, message)[:500],
                                  "at": utcnow().isoformat()}
            db.commit()
        if not candidates:  # a stated rule in the same message is the sharper line; do not add a second
            text = " ".join(_sentences(message)[:2])[:TEXT_MAX]
            candidates.append({"text": text, "type": item.task_type, "excerpt": message, "source": "correction",
                               "item": item, "scope": "", "replaces": []})
        else:
            for c in candidates:
                c["item"] = item

    provider = get_provider()
    for c in candidates:
        if provider.name != "mock":
            _refine(db, user, c, systems)
        if c.get("drop"):
            continue
        result = add(
            db, user, c["text"], c["type"], c["source"], "chat", system_scope=c["scope"],
            origin_excerpt=c["excerpt"], origin_work_item_id=c["item"].id if c["item"] else None,
        )
        if result.created and c["replaces"]:
            result.superseded_ids += supersede(db, user.id, c["replaces"], result.preference)
        noted.append(result)
    return noted


def _refine(db: Session, user: User, c: dict, systems: set[str] | None) -> None:
    """Path 3: let the model rewrite one candidate. Any failure leaves it as it was."""
    try:
        task_type = c["type"]
        current = db.scalars(
            select(Preference)
            .where(Preference.user_id == user.id, Preference.active.is_(True), Preference.task_type == task_type)
            .order_by(Preference.created_at.desc())
            .limit(CURRENT_FOR_EXTRACTOR)
        ).all()
        listing = "; ".join(f"[{p.id}] {p.text}" for p in current) or "(none)"
        prompt = render_extractor(
            redaction.scrub(db, user.id, c["excerpt"])[:1500],
            ", ".join(tasktypes.TYPES),
            ", ".join(sorted(systems or [])),
            listing,
        )
        resp = get_provider().complete([
            {"role": "system", "content": "You extract lasting preferences. JSON only."},
            {"role": "user", "content": prompt},
        ])
        match = re.search(r"\{.*\}", resp.text or "", re.S)
        if not match:
            return
        data = json.loads(match.group(0))
        if not isinstance(data, dict):
            return
        if not data.get("is_preference"):
            if c["source"] == "correction":
                c["drop"] = True  # "no, that's wrong" with nothing lasting in it is not a preference
            return
        text = str(data.get("text") or "").strip()
        if text:
            c["text"] = text[:TEXT_MAX]
        new_type = _normalise_type(str(data.get("task_type") or ""))
        if str(data.get("task_type") or "").strip().lower() in tasktypes.PREFERENCE_TYPES:
            c["type"] = new_type
        system = str(data.get("system") or "").strip()
        if system and systems and system in systems:
            c["scope"] = system
        offered = {p.id for p in current}
        c["replaces"] = [str(r) for r in (data.get("replaces") or []) if str(r) in offered]
    except Exception as exc:  # the plain text stands
        log.warning("Preference refinement failed, keeping the plain text: %s", exc)


# ---------------------------------------------------------------------------
# Path 4: the aura_note_preference tool
# ---------------------------------------------------------------------------


def note_from_tool(
    db: Session,
    user: User,
    conversation: Conversation | None,
    owner_message: str,
    *,
    text: str,
    task_type: str,
    quote: str,
    trigger: str = "chat",
    origin_message_id: str | None = None,
) -> dict:
    """The model asks to keep a preference. Accepted only for a chat turn with learning allowed,
    and only when `quote` is a substring of the owner's own message of this turn."""
    if trigger != "chat":
        return {"saved": False, "reason": "Preferences are only learned from the owner's own chat messages"}
    if not _enabled(db, user, conversation):
        return {"saved": False, "reason": "Memory is paused or off for this chat"}
    needle = _squash(quote)
    if len(needle) < 4 or needle not in _squash(owner_message):
        return {"saved": False, "reason": "The quote is not from the owner's message"}
    if not (text or "").strip():
        return {"saved": False, "reason": "No text"}
    result = add(db, user, text, task_type, "stated", "chat", origin_message_id=origin_message_id,
                 origin_excerpt=quote)
    return {"saved": True, "id": result.preference.id, "duplicate": not result.created,
            "superseded_id": result.superseded_id}


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def _scope_label(scope: str) -> str:
    if not scope:
        return ""
    try:
        from app.plugins import CATALOG

        plugin = CATALOG.get(scope)
        return plugin.name if plugin else scope
    except Exception:
        return scope


def select_rows(
    db: Session,
    user_id: str,
    task_type: str,
    systems: set[str] | None,
) -> list[Preference]:
    """Active lines for the task type and `general`, within the assigned systems: typed first,
    then general, newest first within each. Uncapped and without side effects."""
    task_type = _normalise_type(task_type)
    allowed_scopes = {""} | set(systems or ())
    kinds = {task_type, tasktypes.GENERAL}
    rows = db.scalars(
        select(Preference).where(
            Preference.user_id == user_id,
            Preference.active.is_(True),
            Preference.task_type.in_(kinds),
            Preference.system_scope.in_(allowed_scopes),
        )
    ).all()
    # created_at ties (bulk imports) fall back to id so the order is stable
    rows.sort(key=lambda p: (p.task_type == tasktypes.GENERAL, -_ts(p.created_at), p.id))
    return rows


def _ts(value) -> float:
    if value is None:
        return 0.0
    return (value if value.tzinfo else value.replace(tzinfo=utcnow().tzinfo)).timestamp()


def format_block(
    rows: list[Preference],
    task_type: str,
    noted: list[str] | None = None,
    *,
    max_lines: int = BLOCK_LINES,
    max_chars: int = BLOCK_CHARS,
) -> tuple[str, list[Preference]]:
    """The prompt text for `rows` (already ordered) and the rows that fit.

    Past the line or character cap the general lines go first, oldest first; typed lines last.
    """
    kept = list(rows)

    def lines_of(sel: list[Preference]) -> list[str]:
        typed = [p for p in sel if p.task_type != tasktypes.GENERAL]
        general = [p for p in sel if p.task_type == tasktypes.GENERAL]
        out: list[str] = []
        if typed:
            what = tasktypes.TYPES.get(task_type, "").lower()
            head = f"How this person wants {what} work done" if what and task_type != "other" else \
                "How this person wants this kind of work done"
            out.append(head + " (follow these; newer lines win):")
            out += [_line(p) for p in typed]
        if general:
            out.append("General preferences:")
            out += [_line(p) for p in general]
        return out

    def too_big(sel: list[Preference]) -> bool:
        body = [l for l in lines_of(sel) if l.startswith("- ")]
        return len(body) > max_lines or len("\n".join(lines_of(sel))) > max_chars

    while kept and too_big(kept):
        general = [p for p in kept if p.task_type == tasktypes.GENERAL]
        if general:
            kept.remove(general[-1])  # rows are newest first: the last one is the oldest
        else:
            kept.pop()  # typed lines: the oldest of them
    lines = lines_of(kept)
    if noted:
        lines.append("Noted this turn: " + "; ".join(noted))
    return "\n".join(lines), kept


def _line(p: Preference) -> str:
    scope = _scope_label(p.system_scope)
    return f"- {p.text}" + (f" [{scope} only]" if scope else "")


def mark_used(db: Session, rows: list[Preference]) -> None:
    now = utcnow()
    for p in rows:
        p.use_count = (p.use_count or 0) + 1
        p.last_used_at = now
    if rows:
        db.commit()


def preference_block(
    db: Session,
    user_id: str,
    task_type: str,
    systems: set[str] | None,
    *,
    noted: list[str] | None = None,
    memory_off: bool = False,
) -> tuple[str, list[str]]:
    """(prompt text, ids used). Empty when the chat has memory off; still shown when only paused.
    Bumps use_count / last_used_at on the lines it returns."""
    if memory_off:
        return "", []
    rows = select_rows(db, user_id, task_type, systems)
    text, kept = format_block(rows, task_type, noted)
    mark_used(db, kept)
    return text, [p.id for p in kept]


# ---------------------------------------------------------------------------
# Settings, skills, feedback, reset
# ---------------------------------------------------------------------------


def listing(db: Session, user_id: str, task_type: str | None = None, include_inactive: bool = False) -> list[Preference]:
    stmt = select(Preference).where(Preference.user_id == user_id)
    if not include_inactive:
        stmt = stmt.where(Preference.active.is_(True))
    if task_type:
        stmt = stmt.where(Preference.task_type == _normalise_type(task_type))
    return list(db.scalars(stmt.order_by(Preference.created_at.desc(), Preference.id)).all())


def update(
    db: Session,
    pref: Preference,
    *,
    text: str | None = None,
    task_type: str | None = None,
    system_scope: str | None = None,
    active: bool | None = None,
) -> Preference:
    """The owner's edit in Settings (no conflict resolution: they see the list and chose)."""
    if text is not None:
        clean = re.sub(r"\s+", " ", redaction.scrub(db, pref.user_id, text.strip()))[:TEXT_MAX].strip()
        if not clean:
            raise ValueError("A preference needs some text")
        pref.text = clean
    if task_type is not None:
        pref.task_type = _normalise_type(task_type)
    if system_scope is not None:
        pref.system_scope = system_scope.strip()[:60]
    if active is not None:
        pref.active = active
        if active:
            pref.superseded_by = None
    db.commit()
    db.refresh(pref)
    return pref


def delete(db: Session, pref: Preference) -> None:
    db.delete(pref)
    db.commit()


def reset(db: Session, user_id: str) -> int:
    """Delete every preference of the user; returns how many."""
    rows = db.scalars(select(Preference).where(Preference.user_id == user_id)).all()
    # Self-references (superseded_by) are SET NULL by the database; clear them first for SQLite
    # sessions that run without enforcement.
    for p in rows:
        p.superseded_by = None
    db.flush()
    for p in rows:
        db.delete(p)
    db.commit()
    return len(rows)


def teach(db: Session, user: User, code: str, note: str) -> Captured:
    """`POST /api/skills/{code}/teach`: a note the owner typed against a skill."""
    return add(db, user, note, tasktypes.from_skill_code(code), "stated", "skill_teach")


def skill_notes(db: Session, user_id: str, code: str) -> list[str]:
    """What `SkillOut.learned_notes` shows: active teach / migrated lines of the skill's type."""
    task_type = tasktypes.from_skill_code(code)
    rows = db.scalars(
        select(Preference).where(
            Preference.user_id == user_id, Preference.active.is_(True), Preference.task_type == task_type,
            Preference.origin.in_(("skill_teach", "migrated")),
        ).order_by(Preference.created_at, Preference.id)
    ).all()
    return [p.text for p in rows]


def clear_skill_notes(db: Session, user_id: str, code: str) -> int:
    """`DELETE /api/skills/{code}/notes`: deactivate those lines."""
    task_type = tasktypes.from_skill_code(code)
    rows = db.scalars(
        select(Preference).where(
            Preference.user_id == user_id, Preference.active.is_(True), Preference.task_type == task_type,
            Preference.origin.in_(("skill_teach", "migrated")),
        )
    ).all()
    for p in rows:
        p.active = False
    db.commit()
    return len(rows)


def feedback_note(db: Session, user: User, item: WorkItem, note: str) -> Captured | None:
    """A down-vote with a note is a correction preference (origin `feedback`)."""
    note = (note or "").strip()
    if not note:
        return None
    return add(db, user, note, item.task_type, "correction", "feedback", origin_excerpt=note,
               origin_work_item_id=item.id)
