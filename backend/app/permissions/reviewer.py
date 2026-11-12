"""The auto-mode safety reviewer: interface only (spec 10 section 7). Work package 10 fills it in.

Until then the reviewer is reported unavailable, so a user in `auto` is treated
as `ask` (spec 7.7) and nothing is ever allowed by a reviewer that does not exist.
The gate calls exactly these two names, so WP10 replaces the bodies and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewerVerdict:
    allow: bool
    reason: str = ""
    code: str = ""  # REVIEWER_BLOCK | REVIEWER_ERROR | a pre-block code
    model: str = ""
    latency_ms: int = 0
    tokens: int = 0


def available() -> bool:
    """False until the reviewer exists (and always false for the keyword mock provider)."""
    return False


def review(db, user, policy, args, targets, ctx) -> ReviewerVerdict:
    """Not implemented yet: fails closed."""
    return ReviewerVerdict(False, "The reviewer is not available.", "REVIEWER_ERROR")
