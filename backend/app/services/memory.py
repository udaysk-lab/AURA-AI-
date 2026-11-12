"""Long-term memory: write, embed, and retrieve by semantic similarity.

Retrieval strategy, in order of what's available:
  1. Cosine similarity over stored embeddings (needs an embedding-capable provider)
  2. Lexical overlap fallback (works offline, no keys, no extra infra)

Kinds: Memory holds facts about the world the user works in: fact, contact, project,
decision. How the user wants work done lives in the preference store (services/preferences.py)
and the record of each assignment in task memory (services/workmemory.py); `compact` never
reads either of them.

Storage note: embeddings live in a JSON column so the identical schema runs on
SQLite and Postgres. That means similarity is computed in Python - fine to the
low tens of thousands of memories per user. Past that, migrate the column to
pgvector and swap `_rank` for an ORDER BY ... <=> query. See README.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import get_provider
from app.models import Memory, utcnow

log = logging.getLogger("aura.memory")

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "for", "with", "at", "by", "from", "that", "this",
    "it", "as", "i", "my", "me", "you", "your", "we", "our", "do", "does", "did",
    "have", "has", "had", "will", "would", "can", "could", "should", "about",
}


# The kinds a Memory row may have (spec 20 section 10.6). Rows written by 1.0 with the kinds
# preference / style / habit are copied to the preference store by revision tl02 and are no
# longer read, searched or compacted.
KINDS: tuple[str, ...] = ("fact", "contact", "project", "decision")
LEGACY_KINDS: tuple[str, ...] = ("preference", "style", "habit")
MAX_CONTENT = 2000


@dataclass
class ScoredMemory:
    memory: Memory
    score: float


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _STOPWORDS}


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _lexical(query: str, content: str) -> float:
    q, c = _tokens(query), _tokens(content)
    if not q or not c:
        return 0.0
    return len(q & c) / len(q | c)  # Jaccard


# Public names for the stores that score text the same way (work memory, preferences).
tokens = _tokens
cosine = _cosine
lexical = _lexical


def embed_text(text: str) -> list[float]:
    """Best-effort embedding. Returns [] when the provider can't embed."""
    try:
        vectors = get_provider().embed([text])
        return vectors[0] if vectors else []
    except Exception as exc:
        log.warning("Embedding failed, falling back to lexical search: %s", exc)
        return []


def remember(
    db: Session,
    user_id: str,
    content: str,
    kind: str = "fact",
    source: str = "chat",
    confidence: float = 0.7,
    pinned: bool = False,
) -> Memory:
    """Store a memory, de-duplicating against near-identical existing entries."""
    content = content.strip()
    if kind in LEGACY_KINDS:
        # Until the loop's own preference capture replaces the callers that still pass these
        # kinds, keep what they save findable instead of filing it where nothing reads it.
        kind = "fact"
    existing = db.scalars(
        select(Memory).where(Memory.user_id == user_id, Memory.kind == kind)
    ).all()
    for m in existing:
        if _lexical(content, m.content) > 0.85:
            m.confidence = max(m.confidence, confidence)
            m.updated_at = utcnow()
            db.commit()
            return m

    memory = Memory(
        user_id=user_id,
        content=content,
        kind=kind,
        source=source,
        confidence=confidence,
        pinned=pinned,
        embedding=embed_text(content),
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def search(
    db: Session, user_id: str, query: str, limit: int = 6, min_score: float = 0.05
) -> list[ScoredMemory]:
    rows = db.scalars(
        select(Memory).where(Memory.user_id == user_id, Memory.kind.in_(KINDS))
    ).all()
    if not rows:
        return []

    q_vec = embed_text(query)
    scored: list[ScoredMemory] = []
    for m in rows:
        if q_vec and m.embedding:
            score = _cosine(q_vec, list(m.embedding))
        else:
            score = _lexical(query, m.content)
        if m.pinned:
            score += 0.25  # pinned memories bias into context
        if score >= min_score:
            scored.append(ScoredMemory(memory=m, score=score))

    scored.sort(key=lambda s: s.score, reverse=True)
    top = scored[:limit]

    for s in top:
        s.memory.use_count += 1
        s.memory.last_used_at = utcnow()
    db.commit()
    return top


def compact(db: Session, user_id: str, dry_run: bool = False) -> dict:
    """Consolidate memory. Run periodically from the worker.

    Three passes, cheapest first:
      1. Merge near-duplicates, keeping the longer phrasing and the higher confidence.
      2. Promote memories the assistant keeps reaching for — they belong in every prompt.
      3. Decay memories that were never useful, and drop the ones that decay to nothing.

    Without this, memory only grows, retrieval quality drops as near-duplicates
    crowd each other out, and every prompt gets more expensive for less signal.
    """
    # Only facts, contacts, projects and decisions, and never a row the user wrote or edited
    # by hand. Work items and preferences are not in this table and are never touched.
    rows = db.scalars(
        select(Memory).where(
            Memory.user_id == user_id, Memory.kind.in_(KINDS), Memory.source != "manual"
        )
    ).all()
    merged, promoted, dropped = 0, 0, 0

    # 1. Merge near-duplicates.
    survivors: list[Memory] = []
    for candidate in sorted(rows, key=lambda m: (-m.confidence, -len(m.content))):
        duplicate_of = next(
            (s for s in survivors if _lexical(candidate.content, s.content) > 0.75), None
        )
        if duplicate_of is None:
            survivors.append(candidate)
            continue
        duplicate_of.confidence = max(duplicate_of.confidence, candidate.confidence)
        duplicate_of.use_count += candidate.use_count
        duplicate_of.pinned = duplicate_of.pinned or candidate.pinned
        if len(candidate.content) > len(duplicate_of.content):
            duplicate_of.content = candidate.content
            duplicate_of.embedding = candidate.embedding
        merged += 1
        if not dry_run:
            db.delete(candidate)

    # 2 & 3. Promote what earns it, decay what doesn't.
    for m in survivors:
        if not m.pinned and m.use_count >= 5 and m.confidence >= 0.8:
            m.pinned = True
            promoted += 1
        elif m.use_count == 0 and not m.pinned:
            m.confidence = round(m.confidence * 0.9, 3)
            if m.confidence < 0.25:
                dropped += 1
                if not dry_run:
                    db.delete(m)

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return {
        "before": len(rows),
        "after": len(rows) - merged - dropped,
        "merged": merged,
        "promoted": promoted,
        "dropped": dropped,
    }


def build_context_block(db: Session, user_id: str, query: str, limit: int = 6) -> tuple[str, list[str]]:
    """Return (prompt_fragment, memory_ids) for injection into the system prompt.

    Only relevant memories are surfaced - unrelated personal data never enters
    the prompt, which is both a privacy property and a quality one.
    """
    hits = search(db, user_id, query, limit=limit)
    if not hits:
        return "", []
    lines = [f"- [{h.memory.kind}] {h.memory.content}" for h in hits]
    block = "What you know about this user (retrieved for this request):\n" + "\n".join(lines)
    return block, [h.memory.id for h in hits]


def edit(
    db: Session,
    memory: Memory,
    *,
    content: str | None = None,
    kind: str | None = None,
    pinned: bool | None = None,
) -> Memory:
    """The owner's edit of a memory: re-embeds changed content and marks the row `manual`,
    so compaction never merges, promotes or decays it afterwards.

    Raises ValueError for an empty or over-long content or a kind outside KINDS.
    """
    if content is not None:
        content = content.strip()
        if not content or len(content) > MAX_CONTENT:
            raise ValueError(f"Content must be 1 to {MAX_CONTENT} characters")
    if kind is not None and kind not in KINDS:
        raise ValueError(f"Kind must be one of: {', '.join(KINDS)}")
    if content is not None and content != memory.content:
        memory.content = content
        memory.embedding = embed_text(content)
    if kind is not None:
        memory.kind = kind
    if pinned is not None:
        memory.pinned = pinned
    memory.source = "manual"
    memory.updated_at = utcnow()
    db.commit()
    db.refresh(memory)
    return memory
