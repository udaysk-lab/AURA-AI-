"""Recording a change to what AURA may do (spec 10 section 8.5, point 6).

Every successful permission change by the user writes a permission event (actor `user`) and a
notification to the user: `urgent` when the change widens what AURA can do, `info` otherwise. On a
local install it is also shown as a desktop notification when the computer can show one. A refused
attempt by a program AURA started is logged as `self_change_blocked` with an urgent notification.

Routes call `record(...)` after the change is saved. The event is the audit trail; the
notification is how the person finds out about something they did not do themselves.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Notification, User
from app.permissions import events

log = logging.getLogger("aura.permissions.changes")


def _desktop(title: str, body: str) -> None:
    if not settings.local_mode:
        return
    try:
        from app.device import system

        system.desktop_notification(title[:80], body[:200])
    except Exception:  # no notifier, or device control off: the in-app notification stands
        pass


def record(
    db: Session,
    user: User,
    kind: str,
    summary: str,
    *,
    widens: bool = False,
    tool: str = "",
    target: str = "",
    detail: dict | None = None,
    link: str = "/settings/permissions",
) -> None:
    """Event + notification for one change the signed-in user made. Never raises: the change is saved."""
    try:
        events.append(db, user.id, kind, actor="user", tool=tool, target=target, outcome="allow",
                      detail=detail or {})
    except Exception:
        log.exception("could not write the permission event for %s", kind)
    title = ("Permission widened: " if widens else "Permission changed: ") + summary
    try:
        db.add(Notification(user_id=user.id, level="urgent" if widens else "info", title=title[:300],
                            body="You made this change in AURA. If it was not you, turn it back in Settings, Permissions.",
                            link=link))
        db.commit()
    except Exception:
        log.exception("could not notify about %s", kind)
        db.rollback()
    _desktop("AURA: " + ("a permission was widened" if widens else "a permission changed"), summary)


def self_change_blocked(db: Session, user: User, what: str) -> None:
    try:
        events.append(db, user.id, "self_change_blocked", actor="system", target=what[:200], outcome="deny",
                      reason_code="SPAWNED_PROCESS", detail={"what": what[:200]})
    except Exception:
        log.exception("could not log a refused self-change")
    try:
        db.add(Notification(
            user_id=user.id, level="urgent", link="/settings/permissions",
            title="Something running on this computer tried to change AURA's permissions",
            body="It was refused. If you did not expect this, review what AURA has been asked to run.",
        ))
        db.commit()
    except Exception:
        log.exception("could not notify about a refused self-change")
        db.rollback()
