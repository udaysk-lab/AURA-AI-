"""Email triage: importance, category, summary, action items.

Two-tier by design. Heuristics run always (fast, free, deterministic). The
model pass runs on top when a provider is configured, and only for messages the
heuristics couldn't confidently place — that keeps token spend proportional to
actual ambiguity rather than inbox volume.
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import get_provider
from app.models import EmailMessage

log = logging.getLogger("aura.triage")

URGENT_PATTERNS = [
    r"\burgent\b", r"\basap\b", r"\bimmediately\b", r"\bdeadline\b",
    r"\bby (today|tomorrow|end of day|eod)\b", r"\boverdue\b",
    r"\baction required\b", r"\bfinal notice\b", r"\bneed your (signature|approval)\b",
]
LOW_PATTERNS = [
    r"\bunsubscribe\b", r"\bnewsletter\b", r"\bdigest\b", r"\bpromotion\b",
    r"\bno.?reply@", r"\bwebinar\b",
]
REPLY_PATTERNS = [
    r"\?", r"\bcan you\b", r"\bcould you\b", r"\bwould you\b", r"\blet me know\b",
    r"\bconfirm\b", r"\bthoughts\?", r"\bwhat do you think\b", r"\bplease (advise|review|reply)\b",
]

CATEGORY_HINTS = {
    "Finance": [r"invoice", r"payment", r"receipt", r"billing", r"subscription"],
    "Investors": [r"term sheet", r"cap table", r"\bvc\b", r"investor", r"due diligence"],
    "Hiring": [r"candidate", r"resume", r"cv\b", r"interview", r"recruit"],
    "Engineering": [r"pull request", r"\bpr #", r"deploy", r"incident", r"bug report"],
    "Newsletter": [r"unsubscribe", r"newsletter", r"digest"],
    "Meetings": [r"calendar invite", r"reschedul", r"availability", r"book a time"],
}


def _matches(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def heuristic_triage(email: EmailMessage) -> dict:
    haystack = f"{email.subject}\n{email.snippet}\n{email.body[:2000]}\n{email.sender}"

    if _matches(URGENT_PATTERNS, haystack):
        importance = "urgent"
    elif _matches(LOW_PATTERNS, haystack):
        importance = "low"
    else:
        importance = "normal"

    category = ""
    for name, patterns in CATEGORY_HINTS.items():
        if _matches(patterns, haystack):
            category = name
            break

    needs_reply = importance != "low" and _matches(REPLY_PATTERNS, haystack)
    confident = importance in ("urgent", "low") or bool(category)

    return {
        "importance": importance,
        "category": category or "General",
        "needs_reply": needs_reply,
        "confident": confident,
    }


MODEL_PROMPT = """Classify this email for an executive's inbox.

Return ONLY valid JSON with these keys:
  importance: one of "urgent", "high", "normal", "low"
  category: a short label (e.g. Finance, Investors, Hiring, Engineering, Newsletter, Personal)
  needs_reply: boolean
  summary: one sentence, under 25 words
  action_items: array of short imperative strings (empty if none)

From: {sender}
Subject: {subject}

{body}"""


def model_triage(email: EmailMessage) -> dict | None:
    provider = get_provider()
    if provider.name == "mock":
        return None
    try:
        resp = provider.complete(
            [
                {"role": "system", "content": "You are a precise email triage classifier. Output JSON only."},
                {
                    "role": "user",
                    "content": MODEL_PROMPT.format(
                        sender=f"{email.sender_name} <{email.sender}>",
                        subject=email.subject,
                        body=email.body[:4000] or email.snippet,
                    ),
                },
            ]
        )
        raw = resp.text.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        return json.loads(match.group(0))
    except Exception as exc:
        log.warning("Model triage failed for %s: %s", email.id, exc)
        return None


def triage_email(email: EmailMessage, use_model: bool = True) -> EmailMessage:
    base = heuristic_triage(email)
    email.importance = base["importance"]
    email.category = base["category"]
    email.needs_reply = base["needs_reply"]
    if not email.ai_summary:
        email.ai_summary = (email.snippet or email.body[:160]).strip()

    if use_model and not base["confident"]:
        enriched = model_triage(email)
        if enriched:
            email.importance = enriched.get("importance", email.importance)
            email.category = enriched.get("category", email.category)
            email.needs_reply = bool(enriched.get("needs_reply", email.needs_reply))
            email.ai_summary = enriched.get("summary", email.ai_summary)
            email.action_items = enriched.get("action_items", email.action_items) or []
    return email


def triage_pending(db: Session, user_id: str, limit: int = 50) -> int:
    """Triage any cached email that hasn't been classified yet."""
    rows = db.scalars(
        select(EmailMessage)
        .where(EmailMessage.user_id == user_id, EmailMessage.importance == "normal")
        .where(EmailMessage.category == "")
        .limit(limit)
    ).all()
    for email in rows:
        triage_email(email)
    db.commit()
    return len(rows)
