"""Background worker.

Four loops on one timer:
  * automation rules (fixed tool lists)  — every minute
  * schedules (full agent prompts)       — every minute
  * heartbeat (proactive work)           — per-user interval, checked every minute
  * memory compaction                    — once a day

Run alongside the API:  python worker.py
In production replace with Celery beat / APScheduler, or a platform cron hitting
POST /api/automations/tick and POST /api/heartbeat/run.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.automation import engine
from app.db import SessionLocal, init_db
from app.models import User
from app.services import heartbeat as heartbeat_service
from app.services import memory as memory_service
from app.services import schedules as schedule_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("aura.worker")

TICK_SECONDS = 60
COMPACTION_INTERVAL = timedelta(hours=24)


def compact_all(db) -> None:
    users = db.scalars(select(User).where(User.is_active == True)).all()  # noqa: E712
    for user in users:
        try:
            result = memory_service.compact(db, user.id)
            if result["merged"] or result["dropped"] or result["promoted"]:
                log.info(
                    "Compacted memory for %s: %s merged, %s promoted, %s dropped",
                    user.email, result["merged"], result["promoted"], result["dropped"],
                )
        except Exception:
            log.exception("Compaction failed for %s", user.email)
            db.rollback()


def tick(compact: bool = False) -> None:
    """One pass over all four loops. Never raises — a bad tick must not kill the
    process, and on a cron platform a non-zero exit reads as a failed job."""
    db = SessionLocal()
    try:
        fired = engine.run_due_schedules(db)
        if fired:
            log.info("Fired %d automation rule(s)", len(fired))

        ran = schedule_service.run_due(db)
        if ran:
            log.info("Ran %d schedule(s): %s", len(ran), ", ".join(r["schedule"] for r in ran))

        reports = heartbeat_service.run_all_due(db)
        if reports:
            log.info("Ran %d heartbeat(s)", len(reports))

        if compact:
            compact_all(db)
    except Exception:
        log.exception("Worker tick failed")
        db.rollback()
    finally:
        db.close()


def run_once() -> None:
    """A single pass, for platform cron.

    Free-tier hosts sleep idle services, so a `while True` timer inside the web
    process fires unpredictably or not at all. Running the same work from cron
    makes the schedule real. Compaction is cheap and idempotent, so it just runs
    on every invocation rather than needing state to track the last run.
    """
    init_db()
    log.info("Worker: single pass")
    tick(compact=True)
    log.info("Worker: done")


def main() -> None:
    init_db()
    log.info("Worker started — schedules + heartbeat every %ss, compaction daily", TICK_SECONDS)
    last_compaction = datetime.now(timezone.utc) - COMPACTION_INTERVAL

    while True:
        now = datetime.now(timezone.utc)
        due_for_compaction = now - last_compaction >= COMPACTION_INTERVAL
        tick(compact=due_for_compaction)
        if due_for_compaction:
            last_compaction = now
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    import sys

    if "--once" in sys.argv:
        run_once()
    else:
        main()
