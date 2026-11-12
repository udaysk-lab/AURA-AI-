"""The 1.0 mapping, after the fact (spec 10 section 12).

The mapping itself is the data migration `pm02` (alembic/versions/pm02_permissions_data.py): it
runs once, in SQL, and writes the user's mode, rules, folders and approval expiries. A revision
cannot sign an entry of the tamper-evident log (the key is derived from the server secret, which a
revision must not read), so it leaves a note in `user_settings.extra["legacy_events_pending"]`.
`emit_pending_events` turns each note into one `legacy_mapped` event (actor `migration`) the first
time the app looks at that user's permissions, and clears it. Nothing here changes what anyone may do.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User, UserSettings
from app.permissions import events

log = logging.getLogger("aura.permissions.legacy")

PENDING_KEY = "legacy_events_pending"


def emit_pending_events(db: Session, user: User) -> int:
    """Write the user's waiting `legacy_mapped` events. Returns how many. Never raises."""
    try:
        row = user.settings or db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).first()
        pending = list((row.extra or {}).get(PENDING_KEY) or []) if row else []
        if not pending:
            return 0
        for summary in pending:
            events.append(db, user.id, "legacy_mapped", actor="migration", outcome="allow",
                          detail=dict(summary) if isinstance(summary, dict) else {"note": str(summary)})
        extra = dict(row.extra or {})
        extra.pop(PENDING_KEY, None)
        row.extra = extra
        db.commit()
        return len(pending)
    except Exception:
        log.exception("could not write the legacy mapping events")
        db.rollback()
        return 0
