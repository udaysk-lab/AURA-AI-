"""Realistic starter data.

Seeded for any user without a connected Google account so every screen has
something to show on first load. Connecting Google adds real data alongside it;
`DELETE /api/settings/demo-data` clears it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AutomationRule,
    CalendarEvent,
    Contact,
    EmailMessage,
    HeartbeatReport,
    Memory,
    Notification,
    Schedule,
    SkillRun,
    Task,
    User,
)

DEMO_DOCUMENT = """# Series A Term Sheet — Summary

## Parties
Lead investor: Northwind Ventures. Co-investors to be confirmed before signing.

## Economics
Pre-money valuation: £18,000,000.
Investment amount: £4,500,000 for 20% on a fully diluted basis.
Option pool: 12% pre-money, created before the round closes. This was 10% in the
previous draft and is the main change from the version circulated last month.

## Liquidation preference
1x non-participating preference. Converts to ordinary on a qualifying exit above
£60,000,000.

## Board
Three seats: one founder, one investor, one independent appointed by mutual
agreement. The investor seat is conditional on the company reaching £2,000,000
of annual recurring revenue; below that threshold the investor holds observer
rights only.

## Protective provisions
Standard consent rights over new share classes, debt above £500,000, and any
sale of the company.

## Conditions
Signature required before the investment committee meeting on Thursday at 10:00.
Legal and financial due diligence to complete within 30 days of signing.

## Notice period
Either party may withdraw with 14 days' written notice prior to completion.
"""

DEMO_LABEL = "demo"


def _at(base: datetime, day_offset: int, hour: int, minute: int = 0) -> datetime:
    return (base + timedelta(days=day_offset)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


EMAILS = [
    dict(
        sender="priya@northwind.vc", sender_name="Priya Raman",
        subject="Term sheet — need your signature by Thursday",
        snippet="Legal has finalised the Series A term sheet. We need a signed copy before Thursday's IC meeting.",
        body=(
            "Hi,\n\nLegal has finalised the Series A term sheet. Two changes from the draft you saw: "
            "the option pool moved to 12% pre-money, and the board seat is now conditional on the "
            "$2M ARR milestone.\n\nWe need a signed copy before Thursday's investment committee "
            "meeting at 10:00. Can you confirm you'll get it back to us by then?\n\nPriya"
        ),
        hours_ago=3, importance="urgent", category="Investors",
        needs_reply=True, is_read=False,
        action_items=["Review revised option pool terms", "Sign and return term sheet before Thursday 10:00"],
        ai_summary="Series A term sheet finalised with two changed terms; signature required before Thursday's IC meeting.",
    ),
    dict(
        sender="marcus@stripe.com", sender_name="Marcus Webb",
        subject="Re: Integration timeline",
        snippet="Confirming we can support the March launch. One dependency on your side...",
        body=(
            "Confirming we can support the March launch date.\n\nOne dependency on your side: we need "
            "your webhook endpoint verified before we can enable live mode. Takes about 10 minutes.\n\n"
            "Happy to jump on a call if easier.\n\nMarcus"
        ),
        hours_ago=7, importance="high", category="Partners",
        needs_reply=True, is_read=False,
        action_items=["Verify webhook endpoint for live mode"],
        ai_summary="Stripe confirms March launch; blocked on webhook endpoint verification from your side.",
    ),
    dict(
        sender="accounts@finlytics.io", sender_name="Finlytics Billing",
        subject="Invoice #4471 is overdue",
        snippet="Your invoice for £1,240 was due on the 22nd and remains unpaid.",
        body="Invoice #4471 for £1,240.00 was due on the 22nd and remains unpaid. Please arrange payment.",
        hours_ago=20, importance="high", category="Finance",
        needs_reply=False, is_read=False,
        action_items=["Pay invoice #4471 (£1,240)"],
        ai_summary="Overdue invoice of £1,240; payment needed.",
    ),
    dict(
        sender="sam@aura-design.co", sender_name="Sam Oduya",
        subject="Dashboard mockups — v3",
        snippet="Attached the third pass. I've tightened the spacing and reworked the empty states.",
        body=(
            "Attached the third pass at the dashboard.\n\nChanges: tightened vertical spacing, reworked "
            "the empty states, and the briefing card now collapses on mobile.\n\nLet me know if you want "
            "to keep the gradient on the header — I'm 50/50 on it."
        ),
        hours_ago=26, importance="normal", category="Design",
        needs_reply=True, is_read=True,
        action_items=["Give feedback on header gradient"],
        ai_summary="Third dashboard mockup pass ready; wants a decision on the header gradient.",
    ),
    dict(
        sender="noreply@github.com", sender_name="GitHub",
        subject="[aura-ai] 3 pull requests need review",
        snippet="PR #212, #214 and #215 are awaiting your review.",
        body="PR #212 (memory retrieval), #214 (calendar conflicts), #215 (auth refresh) are awaiting your review.",
        hours_ago=30, importance="normal", category="Engineering",
        needs_reply=False, is_read=True,
        action_items=["Review PRs #212, #214, #215"],
        ai_summary="Three pull requests awaiting review.",
    ),
    dict(
        sender="hello@thegrowthletter.com", sender_name="The Growth Letter",
        subject="The 5 metrics that actually predict retention",
        snippet="This week: why DAU/MAU is lying to you.",
        body="This week: why DAU/MAU is lying to you, and the three cohort metrics that aren't.",
        hours_ago=38, importance="low", category="Newsletter",
        needs_reply=False, is_read=True, action_items=[],
        ai_summary="Newsletter on retention metrics.",
    ),
    dict(
        sender="talent@hiredeck.com", sender_name="Elena Fischer",
        subject="Senior backend candidate — available immediately",
        snippet="I have a strong Python/FastAPI candidate who just came off a contract.",
        body=(
            "I have a strong Python/FastAPI candidate who just came off a contract at a fintech. "
            "8 years, led a platform team of 6. Available immediately.\n\nWorth a 20-minute intro call?"
        ),
        hours_ago=44, importance="normal", category="Hiring",
        needs_reply=True, is_read=False, action_items=["Decide on intro call with backend candidate"],
        ai_summary="Recruiter offering a senior backend candidate; wants a 20-minute intro call.",
    ),
]

EVENTS = [
    dict(title="Standup", day=0, hour=9, minutes=15, location="Zoom",
         attendees=["team@aura.ai"], description="Daily engineering sync."),
    dict(title="1:1 — Sam (Design)", day=0, hour=11, minutes=30, location="Meet",
         attendees=["sam@aura-design.co"], description="Dashboard v3 review."),
    dict(title="Northwind VC — partner call", day=0, hour=15, minutes=45,
         location="Zoom", attendees=["priya@northwind.vc"],
         description="Term sheet walkthrough ahead of IC."),
    dict(title="Product review", day=1, hour=10, minutes=60, location="Room 2",
         attendees=["team@aura.ai"], description="Q3 roadmap sign-off."),
    dict(title="Stripe integration sync", day=1, hour=14, minutes=30,
         location="Meet", attendees=["marcus@stripe.com"], description="Webhook verification."),
    dict(title="Investment committee", day=2, hour=10, minutes=60,
         location="Northwind offices", attendees=["priya@northwind.vc"],
         description="Signed term sheet required beforehand."),
    dict(title="Deep work — architecture", day=2, hour=14, minutes=120,
         location="", attendees=[], description="No meetings block."),
]

TASKS = [
    dict(title="Sign and return Series A term sheet", priority="urgent", day=1,
         tags=["fundraising"], notes="Option pool now 12% pre-money — confirm this is acceptable."),
    dict(title="Verify Stripe webhook endpoint", priority="high", day=0, tags=["engineering"]),
    dict(title="Pay Finlytics invoice #4471", priority="high", day=0, tags=["finance"]),
    dict(title="Review PRs #212, #214, #215", priority="medium", day=1, tags=["engineering"]),
    dict(title="Decide on header gradient (dashboard v3)", priority="low", day=2, tags=["design"]),
    dict(title="Draft Q3 board update", priority="medium", day=5, tags=["board"]),
]

MEMORIES = [
    ("preference", "Prefers meetings between 10:00 and 16:00; protects mornings before 10:00 for deep work.", True),
    ("preference", "Wants email replies drafted short — three sentences or fewer unless asked otherwise.", True),
    ("style", "Writes in British English, plain and direct, no exclamation marks.", True),
    ("contact", "Priya Raman is the lead partner at Northwind VC, running the Series A.", False),
    ("contact", "Sam Oduya is the contract product designer, works Tuesdays to Thursdays.", False),
    ("project", "Series A round is being led by Northwind; target close is end of Q3.", False),
    ("habit", "Reviews the inbox once at 09:00 and once at 16:00, not continuously.", False),
    ("decision", "Chose FastAPI over Django for the backend to keep the agent tooling layer thin.", False),
]

CONTACTS = [
    dict(name="Priya Raman", email="priya@northwind.vc", company="Northwind VC",
         role="Partner", relationship_tier="key", notes="Leading the Series A."),
    dict(name="Marcus Webb", email="marcus@stripe.com", company="Stripe",
         role="Solutions Engineer", relationship_tier="normal"),
    dict(name="Sam Oduya", email="sam@aura-design.co", company="Aura Design",
         role="Product Designer", relationship_tier="key"),
    dict(name="Elena Fischer", email="talent@hiredeck.com", company="Hiredeck",
         role="Recruiter", relationship_tier="low"),
]

AUTOMATIONS = [
    dict(
        name="Investor email → task + notify",
        natural_language="When I receive an email from anyone at northwind.vc, create a task and notify me immediately.",
        trigger_type="email_received",
        trigger_config={"from_contains": "northwind.vc"},
        actions=[
            {"tool": "create_task", "arguments": {"title": "Follow up: {subject}", "priority": "high"}},
            {"tool": "notify", "arguments": {"title": "Investor email from {sender}", "level": "urgent"}},
        ],
        requires_confirmation=False,
    ),
    dict(
        name="Morning briefing at 08:00",
        natural_language="Every weekday at 8:00 AM, prepare my daily briefing.",
        trigger_type="schedule",
        trigger_config={"cron": "0 8 * * 1-5"},
        actions=[{"tool": "get_daily_briefing", "arguments": {}}],
        requires_confirmation=False,
    ),
    dict(
        name="Urgent email → draft reply for approval",
        natural_language="If an email is marked urgent and needs a reply, draft a response and ask me to approve it.",
        trigger_type="email_received",
        trigger_config={"importance": "urgent"},
        actions=[{"tool": "draft_reply", "arguments": {}}],
        requires_confirmation=True,
    ),
]


# A believable recent history so the Skills page and activity log aren't empty
# on first load. Tuples of (skill code, trigger, summary, minutes ago).
SKILL_HISTORY = [
    ("EM01", "heartbeat", "Processed 7 emails scanned · 4 unread · 3 flagged · 4 awaiting reply", 12),
    ("CA03", "heartbeat", "No double-bookings found", 12),
    ("MP01", "heartbeat", "Briefed: Northwind VC — partner call · agenda and context ready", 41),
    ("TK01", "heartbeat", "Captured 2 commitment(s) from flagged email", 41),
    ("BR01", "automation", "Briefing ready · 3 meetings · 3 tasks due · 3 urgent emails", 190),
    ("MM01", "chat", "Remembered: Prefers meetings between 10:00 and 16:00", 260),
    ("EM02", "chat", "Drafted reply · Re: Dashboard mockups — v3 · awaiting your review", 300),
    ("TK02", "heartbeat", "6 open tasks ranked by urgency", 380),
    ("SY01", "heartbeat", "Synced 7 emails and 7 events", 420),
]


def seed_user(db: Session, user: User) -> dict:
    """Populate a fresh account. Idempotent - skips if demo emails already exist."""
    existing = db.scalars(
        select(EmailMessage).where(EmailMessage.user_id == user.id)
    ).first()
    if existing:
        return {"seeded": False, "reason": "user already has data"}

    now = datetime.now(timezone.utc)
    today = now.replace(second=0, microsecond=0)

    for i, e in enumerate(EMAILS):
        db.add(
            EmailMessage(
                user_id=user.id,
                external_id=f"demo-{i}",
                thread_id=f"demo-thread-{i}",
                sender=e["sender"],
                sender_name=e["sender_name"],
                recipients=[user.email],
                subject=e["subject"],
                snippet=e["snippet"],
                body=e["body"],
                received_at=now - timedelta(hours=e["hours_ago"]),
                is_read=e["is_read"],
                labels=["INBOX", DEMO_LABEL],
                category=e["category"],
                importance=e["importance"],
                ai_summary=e["ai_summary"],
                action_items=e["action_items"],
                needs_reply=e["needs_reply"],
            )
        )

    for ev in EVENTS:
        start = _at(today, ev["day"], ev["hour"])
        db.add(
            CalendarEvent(
                user_id=user.id,
                title=ev["title"],
                description=ev["description"],
                location=ev["location"],
                start_at=start,
                end_at=start + timedelta(minutes=ev["minutes"]),
                attendees=ev["attendees"],
                source="local",
            )
        )

    for t in TASKS:
        db.add(
            Task(
                user_id=user.id,
                title=t["title"],
                notes=t.get("notes", ""),
                priority=t["priority"],
                due_at=_at(today, t["day"], 17),
                tags=t["tags"],
                source="manual",
            )
        )

    for kind, content, pinned in MEMORIES:
        db.add(
            Memory(
                user_id=user.id, kind=kind, content=content,
                source="seed", confidence=0.9, pinned=pinned, embedding=[],
            )
        )

    for c in CONTACTS:
        db.add(Contact(user_id=user.id, **c))

    for a in AUTOMATIONS:
        db.add(AutomationRule(user_id=user.id, **a))

    for code, trigger, summary, minutes_ago in SKILL_HISTORY:
        run = SkillRun(
            user_id=user.id, code=code, trigger=trigger,
            summary=summary, status="success", detail={"seeded": True},
            duration_ms=180 + len(summary),
        )
        run.created_at = now - timedelta(minutes=minutes_ago)
        run.updated_at = run.created_at
        db.add(run)

    report = HeartbeatReport(
        user_id=user.id,
        headline="Handled 4 things while you were away · 3 need your attention",
        lines=[
            "[SKILL·EM01] Processed 7 emails scanned · 4 unread · 3 flagged · 4 awaiting reply",
            "[SKILL·TK01] Captured 2 commitment(s) from flagged email",
            "[SKILL·MP01] Briefed: Northwind VC — partner call · agenda and context ready",
            "[SKILL·CA03] No double-bookings found",
        ],
        skills_run=["CA03", "EM01", "MP01", "TK01"],
        needs_attention=3,
    )
    report.created_at = now - timedelta(minutes=12)
    report.updated_at = report.created_at
    db.add(report)

    # A starter schedule and a document, so those screens aren't empty either.
    db.add(
        Schedule(
            user_id=user.id,
            name="Morning briefing",
            prompt=(
                "Give me my morning briefing: today's meetings, what's due, anything "
                "urgent in the inbox, and the three things you'd do first."
            ),
            natural_language="every weekday at 7:30am",
            cron="30 7 * * 1-5",
            deliver_to="notification",
        )
    )

    from app.services import documents as document_service

    document_service.ingest(
        db,
        user.id,
        "Series A term sheet (summary)",
        DEMO_DOCUMENT,
        mime="text/markdown",
        source="seed",
    )

    db.add(
        Notification(
            user_id=user.id,
            title="Your workspace is ready",
            body="Sample data is loaded so every screen works. Connect Google in Settings to use it for real.",
            level="info",
            link="/settings",
        )
    )

    db.commit()
    return {
        "seeded": True,
        "emails": len(EMAILS),
        "events": len(EVENTS),
        "tasks": len(TASKS),
        "skill_runs": len(SKILL_HISTORY),
    }
