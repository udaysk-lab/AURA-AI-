"""The plugin catalogue.

A plugin is a bundle of skills you install as a unit. Core plugins ship enabled
and can't be removed — without them there is no assistant. Optional plugins
unlock additional skills; uninstalling one hides its skills from the model
without destroying their learned notes or run history, so reinstalling picks up
where you left off.

Plugins that need an integration we haven't built are listed with
`available=False` and a reason. Showing them greyed out is more honest than
hiding them and more honest still than shipping a stub that pretends to work.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InstalledPlugin

CATEGORIES = [
    "Productivity",
    "Communication",
    "Knowledge",
    "Lifestyle",
    "Developer",
    "Finance",
]


@dataclass(frozen=True)
class Plugin:
    id: str
    name: str
    category: str
    summary: str
    detail: str
    skills: tuple[str, ...]
    core: bool = False
    available: bool = True
    unavailable_reason: str = ""
    accent: str = "teal"
    requires: tuple[str, ...] = field(default_factory=tuple)


CATALOG: dict[str, Plugin] = {
    p.id: p
    for p in [
        # --- Core: always installed -------------------------------------
        Plugin(
            id="core-inbox",
            name="Inbox",
            category="Productivity",
            summary="Read, triage and reply to email.",
            detail=(
                "Scores every message for urgency, extracts the commitments buried in "
                "them, and drafts replies in your voice. Sending always asks first."
            ),
            skills=("EM01", "EM02", "EM03"),
            core=True,
            accent="amber",
        ),
        Plugin(
            id="core-calendar",
            name="Calendar",
            category="Productivity",
            summary="Hold your schedule and prepare you for it.",
            detail=(
                "Finds genuinely free time rather than gaps, catches double-bookings "
                "before you walk into them, and briefs you on who you're about to meet."
            ),
            skills=("CA01", "CA02", "CA03", "MP01"),
            core=True,
            accent="teal",
        ),
        Plugin(
            id="core-tasks",
            name="Tasks",
            category="Productivity",
            summary="Track what you owe people.",
            detail=(
                "Captures commitments from anywhere, ranks them by real urgency rather "
                "than the order you typed them, and rolls recurring work forward."
            ),
            skills=("TK01", "TK02"),
            core=True,
            accent="violet",
        ),
        Plugin(
            id="core-memory",
            name="Memory",
            category="Knowledge",
            summary="Remember how you work.",
            detail=(
                "Stores durable preferences, projects and decisions, retrieves them by "
                "meaning, and consolidates them so recall stays sharp as it grows."
            ),
            skills=("MM01", "CT01"),
            core=True,
            accent="sage",
        ),
        Plugin(
            id="core-planning",
            name="Planning",
            category="Productivity",
            summary="Your day, compiled.",
            detail=(
                "The morning briefing, notifications worth interrupting you for, and "
                "keeping the local mirror of your accounts current."
            ),
            skills=("BR01", "NT01", "SY01"),
            core=True,
            accent="rose",
        ),
        # --- Optional: real ---------------------------------------------
        Plugin(
            id="research",
            name="Research",
            category="Knowledge",
            summary="Look things up properly before you walk in.",
            detail=(
                "Searches the web and compiles a briefing on a company, a person or a "
                "topic — combining what it finds online with what it already knows from "
                "your inbox and contacts. Needs a search provider key; falls back to "
                "your own data alone without one."
            ),
            skills=("RS01", "RS02"),
            accent="violet",
        ),
        Plugin(
            id="documents",
            name="Documents",
            category="Knowledge",
            summary="Answer questions from your own files.",
            detail=(
                "Upload contracts, decks, reports and notes. They're chunked, embedded "
                "and searchable by meaning, so you can ask what a clause says instead of "
                "hunting for the file."
            ),
            skills=("DC01", "DC02"),
            accent="teal",
        ),
        Plugin(
            id="delegation",
            name="Delegation",
            category="Communication",
            summary="Hand things off and actually follow up.",
            detail=(
                "Records what you gave to whom and when it's due, then chases it before "
                "it goes quiet. The chase-up is drafted, never sent without you."
            ),
            skills=("DL01", "DL02"),
            accent="amber",
        ),
        Plugin(
            id="focus",
            name="Focus Guard",
            category="Lifestyle",
            summary="Defend the hours you said were yours.",
            detail=(
                "Learns which blocks you protect, flags anything that lands in them, and "
                "proposes alternatives instead of just letting the meeting through."
            ),
            skills=("FG01",),
            accent="sage",
        ),
        # --- Optional: needs an integration we haven't built --------------
        Plugin(
            id="developer",
            name="Developer",
            category="Developer",
            summary="Triage issues and watch your repos.",
            detail=(
                "Labels and routes incoming GitHub issues by your team's rules, and "
                "summarises what shipped."
            ),
            skills=(),
            available=False,
            unavailable_reason="Needs a GitHub connector — not built yet.",
            accent="violet",
        ),
        Plugin(
            id="travel",
            name="Travel",
            category="Lifestyle",
            summary="Book it and buffer the calendar around it.",
            detail="Flights, hotels, and the travel time either side blocked out properly.",
            skills=(),
            available=False,
            unavailable_reason="Needs a booking provider — not built yet.",
            accent="rose",
        ),
        Plugin(
            id="expenses",
            name="Expenses",
            category="Finance",
            summary="Catch receipts before they're lost.",
            detail="Pulls receipts out of your inbox, categorises them, and totals them up.",
            skills=(),
            available=False,
            unavailable_reason="Needs an accounting connector — not built yet.",
            accent="amber",
        ),
        Plugin(
            id="comms",
            name="Team Comms",
            category="Communication",
            summary="Summarise the channels you can't keep up with.",
            detail="Reads your Slack, surfaces blockers and decisions, keeps threads moving.",
            skills=(),
            available=False,
            unavailable_reason="Needs a Slack connector — not built yet.",
            accent="teal",
        ),
    ]
}

CORE_IDS = {p.id for p in CATALOG.values() if p.core}

# skill code -> owning plugin id
SKILL_TO_PLUGIN: dict[str, str] = {
    code: plugin.id for plugin in CATALOG.values() for code in plugin.skills
}


def ensure_core(db: Session, user_id: str) -> None:
    """Core plugins are installed on first touch and can't be removed."""
    installed = {
        row.plugin_id
        for row in db.scalars(
            select(InstalledPlugin).where(InstalledPlugin.user_id == user_id)
        ).all()
    }
    added = False
    for plugin_id in CORE_IDS:
        if plugin_id not in installed:
            db.add(InstalledPlugin(user_id=user_id, plugin_id=plugin_id, enabled=True))
            added = True
    if added:
        db.commit()


def installed_ids(db: Session, user_id: str) -> set[str]:
    ensure_core(db, user_id)
    return {
        row.plugin_id
        for row in db.scalars(
            select(InstalledPlugin).where(
                InstalledPlugin.user_id == user_id,
                InstalledPlugin.enabled == True,  # noqa: E712
            )
        ).all()
    }


def unlocked_skill_codes(db: Session, user_id: str) -> set[str]:
    """Skill codes the user's installed plugins make available."""
    codes: set[str] = set()
    for plugin_id in installed_ids(db, user_id):
        plugin = CATALOG.get(plugin_id)
        if plugin:
            codes.update(plugin.skills)
    return codes


def install(db: Session, user_id: str, plugin_id: str) -> tuple[bool, str]:
    plugin = CATALOG.get(plugin_id)
    if not plugin:
        return False, "Unknown plugin"
    if not plugin.available:
        return False, plugin.unavailable_reason

    row = db.scalars(
        select(InstalledPlugin).where(
            InstalledPlugin.user_id == user_id, InstalledPlugin.plugin_id == plugin_id
        )
    ).first()
    if row:
        row.enabled = True
    else:
        db.add(InstalledPlugin(user_id=user_id, plugin_id=plugin_id, enabled=True))
    db.commit()

    # Materialise the newly unlocked skills so they show up immediately.
    from app.agent import skills as skill_registry

    skill_registry.ensure_user_skills(db, user_id)
    return True, f"{plugin.name} installed"


def uninstall(db: Session, user_id: str, plugin_id: str) -> tuple[bool, str]:
    plugin = CATALOG.get(plugin_id)
    if not plugin:
        return False, "Unknown plugin"
    if plugin.core:
        return False, f"{plugin.name} is core and can't be removed"

    row = db.scalars(
        select(InstalledPlugin).where(
            InstalledPlugin.user_id == user_id, InstalledPlugin.plugin_id == plugin_id
        )
    ).first()
    if row:
        # Disable rather than delete: the skills' learned notes and run history
        # survive, so reinstalling picks up where you left off.
        row.enabled = False
        db.commit()
    return True, f"{plugin.name} removed"


def summary(db: Session, user_id: str) -> dict:
    live = installed_ids(db, user_id)
    return {
        "installed": len(live),
        "available": sum(1 for p in CATALOG.values() if p.available),
        "total": len(CATALOG),
    }
