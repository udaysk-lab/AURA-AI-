"""Usage accounting and the spend guard.

An agent that loops over tools, plus a heartbeat that fires unattended every
thirty minutes, plus inbound webhooks — that's three ways to spend money without
anyone watching. This is the backstop.

Two deliberate choices:

  * The cap is checked **before** each model call, not after. Refusing to start a
    turn is recoverable; discovering you're $400 over is not.
  * Background work (heartbeat, schedules) is cut off at a *fraction* of the cap,
    so automated spending can never starve the user of the interactive budget
    they actually notice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.llm import Usage
from app.models import UsageRecord

log = logging.getLogger("aura.usage")

# Cost per million tokens, (input, output).
#
# These are ESTIMATES used only for the spend cap — they are not billing. Model
# pricing changes; verify against your provider's current rates and override with
# PRICE_OVERRIDES_JSON if they drift. An unknown model falls back to DEFAULT_PRICE,
# which is deliberately pessimistic so an unrecognised model can't spend freely.
PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-5": (15.00, 75.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "text-embedding-3-small": (0.02, 0.00),
    # Cheap OpenAI-compatible endpoints
    "deepseek-chat": (0.28, 0.42),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
}
DEFAULT_PRICE = (15.00, 75.00)

# Anything served from your own machine costs nothing per token. Matched by
# prefix so `llama3.1:8b`, `qwen2.5:14b` and friends all resolve to free.
LOCAL_MODEL_HINTS = (
    "llama3",
    "llama-3",
    "qwen",
    "mistral",
    "mixtral",
    "phi",
    "gemma",
    "deepseek-r1",
    "smollm",
)

# Share of the daily cap that unattended work may consume.
BACKGROUND_BUDGET_SHARE = 0.4


@dataclass
class SpendState:
    spent_usd: float
    cap_usd: float
    calls: int
    tokens: int

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    @property
    def percent(self) -> int:
        if self.cap_usd <= 0:
            return 0
        return min(100, round(self.spent_usd / self.cap_usd * 100))

    def as_dict(self) -> dict:
        return {
            "spent_usd": round(self.spent_usd, 4),
            "cap_usd": round(self.cap_usd, 2),
            "remaining_usd": round(self.remaining_usd, 4),
            "percent": self.percent,
            "calls": self.calls,
            "tokens": self.tokens,
        }


class SpendCapReached(Exception):
    """Raised instead of making a model call the user can't afford."""

    def __init__(self, state: SpendState, trigger: str):
        self.state = state
        self.trigger = trigger
        super().__init__(
            f"Daily spend cap reached (${state.spent_usd:.2f} of ${state.cap_usd:.2f})."
        )


def price_for(model: str) -> tuple[float, float]:
    if model in PRICES:
        return PRICES[model]
    # Prefix match so "claude-sonnet-5-20260501" resolves to "claude-sonnet-5".
    for known, price in PRICES.items():
        if model.startswith(known):
            return price

    # Self-hosted models cost nothing per token, so charging them against the
    # cap would stop a free setup working for no reason.
    lowered = model.lower()
    if settings.is_local_model or any(h in lowered for h in LOCAL_MODEL_HINTS):
        return (0.0, 0.0)

    log.warning("Unknown model %r — pricing it pessimistically for the cap.", model)
    return DEFAULT_PRICE


def estimate_cost(usage: Usage) -> float:
    if not usage.total:
        return 0.0
    input_price, output_price = price_for(usage.model)
    return (
        usage.input_tokens / 1_000_000 * input_price
        + usage.output_tokens / 1_000_000 * output_price
    )


def _day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def record(
    db: Session,
    user_id: str,
    usage: Usage,
    trigger: str = "chat",
    skill_code: str = "",
) -> UsageRecord | None:
    """Log one model call. Cheap and unconditional — this is the audit trail."""
    if not usage.total:
        return None
    row = UsageRecord(
        user_id=user_id,
        model=usage.model or "unknown",
        trigger=trigger,
        skill_code=skill_code,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=estimate_cost(usage),
    )
    db.add(row)
    db.commit()
    return row


def spend_today(db: Session, user_id: str, now: datetime | None = None) -> SpendState:
    start = _day_start(now)
    row = db.execute(
        select(
            func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.input_tokens + UsageRecord.output_tokens), 0),
        ).where(UsageRecord.user_id == user_id, UsageRecord.created_at >= start)
    ).one()
    return SpendState(
        spent_usd=float(row[0] or 0.0),
        cap_usd=settings.daily_spend_cap_usd,
        calls=int(row[1] or 0),
        tokens=int(row[2] or 0),
    )


def check(db: Session, user_id: str, trigger: str = "chat") -> SpendState:
    """Raise SpendCapReached if this call shouldn't happen. Call before the model."""
    state = spend_today(db, user_id)
    if not settings.spend_cap_enabled or state.cap_usd <= 0:
        return state

    budget = state.cap_usd
    if trigger in ("heartbeat", "schedule", "automation"):
        budget *= BACKGROUND_BUDGET_SHARE

    if state.spent_usd >= budget:
        raise SpendCapReached(state, trigger)
    return state


def history(db: Session, user_id: str, days: int = 14) -> list[dict]:
    """Daily totals, for the usage panel."""
    start = _day_start() - timedelta(days=days - 1)
    rows = db.scalars(
        select(UsageRecord)
        .where(UsageRecord.user_id == user_id, UsageRecord.created_at >= start)
    ).all()

    buckets: dict[str, dict] = {}
    for row in rows:
        created = row.created_at
        created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        key = created.date().isoformat()
        bucket = buckets.setdefault(
            key, {"date": key, "cost_usd": 0.0, "tokens": 0, "calls": 0}
        )
        bucket["cost_usd"] += row.cost_usd
        bucket["tokens"] += row.input_tokens + row.output_tokens
        bucket["calls"] += 1

    return [
        {**b, "cost_usd": round(b["cost_usd"], 4)}
        for b in sorted(buckets.values(), key=lambda b: b["date"])
    ]


def by_trigger(db: Session, user_id: str, days: int = 7) -> list[dict]:
    """Where the money went — interactive chat vs unattended background work."""
    start = _day_start() - timedelta(days=days - 1)
    rows = db.execute(
        select(
            UsageRecord.trigger,
            func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
            func.count(UsageRecord.id),
        )
        .where(UsageRecord.user_id == user_id, UsageRecord.created_at >= start)
        .group_by(UsageRecord.trigger)
    ).all()
    return sorted(
        [
            {"trigger": r[0] or "unknown", "cost_usd": round(float(r[1] or 0), 4), "calls": int(r[2])}
            for r in rows
        ],
        key=lambda r: r["cost_usd"],
        reverse=True,
    )
